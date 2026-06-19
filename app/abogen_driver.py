"""In-process Abogen generation driver.

This script runs INSIDE the provisioned Abogen environment's Python interpreter
(not the app's venv). The app launches it as a subprocess and parses its stdout.

It mirrors exactly what the abogen web UI does on "finish" (see
abogen/webui/routes/main.py -> wizard_finish -> submit_job), but in-process and
non-interactively: extract the book, build a pending job (which performs Abogen's
smart chapter pre-selection), enqueue it on a ConversionService whose runner is
run_conversion_job, then wait for completion and report the produced .m4b + .ass.

Protocol: one JSON object per line on stdout:
  {"event": "log",      "message": "..."}
  {"event": "progress", "percent": 0..100}
  {"event": "done",     "audio": "<path>", "subtitles": ["<path>", ...]}
  {"event": "error",    "message": "..."}

Usage:  python abogen_driver.py <spec.json>
  spec.json keys: input, output_dir, title?, voice, speed, subtitle_mode,
                  language, settings{output_format, subtitle_format, use_gpu,
                  replace_single_newlines, ...}
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path


# Abogen logs to stdout and shells out to ffmpeg, so stdout is noisy. Tag our
# structured events with a sentinel the client filters on.
EVENT_SENTINEL = "@@VAS_EVENT@@"


def emit(obj) -> None:
    sys.stdout.write(EVENT_SENTINEL + json.dumps(obj) + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Karaoke highlighting patch
#
# Abogen's *web* pipeline (which we drive) writes plain .ass subtitles — no
# word-by-word highlighting — even in "Sentence + Highlighting" mode. Its
# *desktop* pipeline does the karaoke. We reproduce the desktop behaviour here:
#   1) hook kokoro.KPipeline.__call__ to capture each segment's per-token
#      timestamps (text/whitespace/start_ts/end_ts), and
#   2) subclass the web SubtitleWriter to emit `{\kf<cs>}` karaoke per word,
#      splitting into sentences exactly like the desktop's _process_subtitle_tokens.
# Everything else (extraction, chunking, m4b+chapters) is unchanged.
# ---------------------------------------------------------------------------
_KARAOKE = {"seg": None, "mode": None, "max_words": 50}
_SENT_RE = re.compile("[" + re.escape(".!?।。！？") + "]")


def _tok(t, name):
    return getattr(t, name, None)


def _karaoke_entries(tokens, seg_start, max_words):
    """Replicates desktop _process_subtitle_tokens for 'Sentence + Highlighting'."""
    entries = []
    cur = []
    count = 0

    def flush():
        if not cur:
            return
        s0 = seg_start + (_tok(cur[0], "start_ts") or 0.0)
        e0 = seg_start + (_tok(cur[-1], "end_ts") or 0.0)
        parts = []
        for t in cur:
            st, en = _tok(t, "start_ts"), _tok(t, "end_ts")
            dur = (en - st) if (st is not None and en is not None and en > st) else 0.5
            parts.append("{\\kf%d}%s%s" % (max(0, int(dur * 100)),
                                           _tok(t, "text") or "", _tok(t, "whitespace") or ""))
        entries.append((s0, e0, "".join(parts).strip()))

    for t in tokens:
        cur.append(t)
        count += 1
        if (_SENT_RE.search(_tok(t, "text") or "") and _tok(t, "whitespace") == " ") or count >= max_words:
            flush()
            cur, count = [], 0
    flush()
    return entries


def _maybe_install_karaoke(cr, mode, max_words):
    if mode != "Sentence + Highlighting":
        return
    _KARAOKE["mode"] = mode
    _KARAOKE["max_words"] = max_words or 50
    try:
        import kokoro
        kp = getattr(kokoro, "KPipeline", None)
        if kp is None:
            from kokoro.pipeline import KPipeline as kp  # type: ignore
        if not getattr(kp.__call__, "_vas_wrapped", False):
            _orig_call = kp.__call__

            def _wrapped(self, *a, **kw):
                for seg in _orig_call(self, *a, **kw):
                    _KARAOKE["seg"] = seg
                    yield seg

            _wrapped._vas_wrapped = True
            kp.__call__ = _wrapped
    except Exception as exc:  # noqa: BLE001
        emit({"event": "log", "message": f"Karaoke hook skipped (kokoro): {exc}"})
        return

    try:
        base = cr.SubtitleWriter
        if getattr(base, "_vas_karaoke", False):
            return
        fmt_ts = cr._format_timestamp

        class _KaraokeWriter(base):
            _vas_karaoke = True

            def write_segment(self, *, index, text, start, end):
                if _KARAOKE.get("mode") == "Sentence + Highlighting" and self.format_key == "ass":
                    seg = _KARAOKE.get("seg")
                    toks = getattr(seg, "tokens", None) if seg is not None else None
                    if toks:
                        wrote = False
                        for s, e, kt in _karaoke_entries(toks, start, _KARAOKE.get("max_words", 50)):
                            if kt:
                                self._file.write("Dialogue: 0,%s,%s,Default,,0000,0000,0000,,%s\n"
                                                 % (fmt_ts(s, ass=True), fmt_ts(e, ass=True), kt))
                                wrote = True
                        if wrote:
                            return
                super().write_segment(index=index, text=text, start=start, end=end)

        cr.SubtitleWriter = _KaraokeWriter
        emit({"event": "log", "message": "Karaoke highlighting enabled."})
    except Exception as exc:  # noqa: BLE001
        emit({"event": "log", "message": f"Karaoke hook skipped (writer): {exc}"})


def _enqueue_from_pending(service, pending):
    """Enqueue a Job from a PendingJob, mirroring webui submit_job()."""
    g = lambda name, default=None: getattr(pending, name, default)
    return service.enqueue(
        original_filename=pending.original_filename,
        stored_path=pending.stored_path,
        language=pending.language,
        tts_provider=g("tts_provider", "kokoro"),
        voice=pending.voice,
        speed=pending.speed,
        supertonic_total_steps=g("supertonic_total_steps", 5),
        use_gpu=pending.use_gpu,
        subtitle_mode=pending.subtitle_mode,
        output_format=pending.output_format,
        save_mode=pending.save_mode,
        output_folder=pending.output_folder,
        replace_single_newlines=pending.replace_single_newlines,
        subtitle_format=pending.subtitle_format,
        total_characters=pending.total_characters,
        chapters=pending.chapters,
        save_chapters_separately=pending.save_chapters_separately,
        merge_chapters_at_end=pending.merge_chapters_at_end,
        separate_chapters_format=pending.separate_chapters_format,
        silence_between_chapters=pending.silence_between_chapters,
        save_as_project=pending.save_as_project,
        voice_profile=pending.voice_profile,
        max_subtitle_words=pending.max_subtitle_words,
        metadata_tags=pending.metadata_tags,
        cover_image_path=pending.cover_image_path,
        cover_image_mime=pending.cover_image_mime,
        chapter_intro_delay=pending.chapter_intro_delay,
        read_title_intro=pending.read_title_intro,
        read_closing_outro=g("read_closing_outro", True),
        auto_prefix_chapter_titles=pending.auto_prefix_chapter_titles,
        normalize_chapter_opening_caps=pending.normalize_chapter_opening_caps,
        chunk_level=pending.chunk_level,
        chunks=pending.chunks,
        speakers=pending.speakers,
        speaker_mode=pending.speaker_mode,
        generate_epub3=pending.generate_epub3,
        speaker_analysis=pending.speaker_analysis,
        speaker_analysis_threshold=pending.speaker_analysis_threshold,
        analysis_requested=pending.analysis_requested,
        entity_summary=g("entity_summary", None),
        manual_overrides=g("manual_overrides", None),
        pronunciation_overrides=g("pronunciation_overrides", None),
        heteronym_overrides=g("heteronym_overrides", None),
        normalization_overrides=pending.normalization_overrides,
    )


def main() -> int:
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    input_path = Path(spec["input"])
    out_dir = Path(spec["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    # Isolate the queue state so we never load/clobber an existing queue.
    state_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    state_file.close()
    os.environ["ABOGEN_QUEUE_STATE_PATH"] = state_file.name

    from abogen.text_extractor import extract_from_path
    from abogen.webui.routes.utils.form import build_pending_job_from_extraction
    from abogen.webui.routes.utils.settings import load_settings
    from abogen.voice_profiles import serialize_profiles
    from abogen.webui.service import ConversionService, JobStatus
    from abogen.webui.conversion_runner import run_conversion_job

    settings = load_settings()
    settings.update(spec.get("settings", {}))   # m4b / ass_centered_wide / gpu / etc.

    form = {
        "voice": spec.get("voice", ""),
        "speed": str(spec.get("speed", 1.0)),
        "subtitle_mode": spec.get("subtitle_mode", "Sentence + Highlighting"),
        "language": spec.get("language", "a"),
    }

    emit({"event": "log", "message": f"Extracting {input_path.name}"})
    extraction = extract_from_path(input_path)

    result = build_pending_job_from_extraction(
        stored_path=input_path,
        original_name=spec.get("title") or input_path.name,
        extraction=extraction,
        form=form,
        settings=settings,
        profiles=serialize_profiles(),
    )
    pending = result.pending

    # Phase 1: just report the chapters (with Abogen's smart pre-selection) and exit.
    if spec.get("mode") == "extract":
        chapters = [
            {
                "index": i,
                "title": ch.get("title") or f"Chapter {i + 1}",
                "characters": int(ch.get("characters") or len(str(ch.get("text", "")))),
                "enabled": bool(ch.get("enabled", True)),
                "preview": str(ch.get("text", ""))[:1500],
            }
            for i, ch in enumerate(pending.chapters)
        ]
        emit({"event": "chapters", "chapters": chapters})
        return 0

    # Phase 2: apply an explicit chapter selection, if one was provided.
    selected = spec.get("selected_indices")
    if selected is not None:
        selset = {int(x) for x in selected}
        for i, ch in enumerate(pending.chapters):
            ch["enabled"] = i in selset
        if not any(ch.get("enabled") for ch in pending.chapters):
            emit({"event": "error", "message": "No chapters were selected."})
            return 1
        # Drop precomputed chunks so the runner synthesizes the exact enabled
        # chapters from their text (precomputed chunk indices would be stale).
        pending.chunks = []

    pending.save_mode = "Choose output folder"
    pending.output_folder = out_dir

    # Reproduce the desktop pipeline's karaoke .ass for "Sentence + Highlighting".
    import abogen.webui.conversion_runner as _cr
    _maybe_install_karaoke(_cr, form.get("subtitle_mode", ""),
                           int(settings.get("max_subtitle_words", 50) or 50))

    emit({"event": "log", "message": "Queued; starting TTS…"})
    service = ConversionService(
        output_root=out_dir,
        runner=run_conversion_job,
        uploads_root=out_dir / "_work",
    )
    job = _enqueue_from_pending(service, pending)

    last_pct = -1.0
    last_log = 0
    terminal = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
    while True:
        # Surface new job logs.
        logs = job.logs
        if len(logs) > last_log:
            for entry in logs[last_log:]:
                emit({"event": "log", "message": entry.message})
            last_log = len(logs)
        pct = round((job.progress or 0.0) * 100.0, 1)
        if pct != last_pct:
            emit({"event": "progress", "percent": pct})
            last_pct = pct
        if job.status in terminal:
            break
        time.sleep(0.4)

    service.shutdown()

    if job.status == JobStatus.COMPLETED:
        audio = str(job.result.audio_path) if job.result.audio_path else ""
        subs = [str(p) for p in job.result.subtitle_paths]
        emit({"event": "done", "audio": audio, "subtitles": subs})
        return 0
    emit({"event": "error", "message": job.error or f"Job ended with status {job.status}"})
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        import traceback
        emit({"event": "error", "message": f"{exc.__class__.__name__}: {exc}"})
        sys.stderr.write(traceback.format_exc())
        raise SystemExit(1)
