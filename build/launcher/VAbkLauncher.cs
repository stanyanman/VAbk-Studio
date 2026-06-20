// Native launcher for VAbk Studio that runs the app FROM SOURCE.
//
// This is the .exe cousin of "Run VAbk-Studio.bat": on first run it provisions a
// .venv with uv (Python 3.12, auto-downloaded if missing) and installs
// requirements.txt, then it launches run.py windowless via pythonw.exe. It is
// NOT a frozen build -- it executes the .py files sitting next to it, so editing
// the source and re-launching just works.
//
// Build (no extra tooling -- csc ships with the .NET Framework):
//   csc /nologo /target:winexe /out:"Run VAbk-Studio.exe" \
//       /r:System.Windows.Forms.dll build\launcher\VAbkLauncher.cs

using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class VAbkLauncher
{
    [STAThread]
    static int Main(string[] args)
    {
        // Folder containing this exe -- works no matter where it's launched from.
        string baseDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');

        string scripts = Path.Combine(baseDir, ".venv", "Scripts");
        string py = Path.Combine(scripts, "python.exe");
        string pyw = Path.Combine(scripts, "pythonw.exe");
        string runPy = Path.Combine(baseDir, "run.py");

        if (!File.Exists(runPy))
        {
            Error("Could not find run.py next to this launcher.\n\nExpected:\n" + runPy +
                  "\n\nKeep this .exe in the VAbk Studio source folder.");
            return 1;
        }

        // First run (or a wiped .venv): provision with uv, showing live progress.
        if (!File.Exists(py))
        {
            string uv = ResolveUv();
            if (uv == null)
            {
                Error("Could not find 'uv' on PATH or in %USERPROFILE%\\.local\\bin.\n\n" +
                      "Install it, then run this launcher again:\n" +
                      "  powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\"\n\n" +
                      "More info: https://docs.astral.sh/uv/");
                return 1;
            }

            string setup =
                "\"" + uv + "\" venv --python 3.12 .venv && " +
                "\"" + uv + "\" pip install --python \".venv\\Scripts\\python.exe\" -r requirements.txt";

            var psi = new ProcessStartInfo("cmd.exe", "/c " + setup)
            {
                WorkingDirectory = baseDir,
                UseShellExecute = true,                 // visible console = download progress
                WindowStyle = ProcessWindowStyle.Normal,
            };
            try
            {
                Process p = Process.Start(psi);
                p.WaitForExit();
                if (p.ExitCode != 0)
                {
                    Error("Setup failed (exit code " + p.ExitCode + ").\n\n" +
                          "Run \"Run VAbk-Studio.bat\" to see the full output.");
                    return p.ExitCode;
                }
            }
            catch (Exception ex)
            {
                Error("Setup could not start:\n\n" + ex.Message);
                return 1;
            }
        }

        // Launch the GUI windowless (prefer pythonw.exe so no console appears).
        string interpreter = File.Exists(pyw) ? pyw : py;
        string passthru = JoinArgs(args);
        string arguments = "\"" + runPy + "\"" + (passthru.Length > 0 ? " " + passthru : "");

        var run = new ProcessStartInfo(interpreter, arguments)
        {
            WorkingDirectory = baseDir,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        try
        {
            Process.Start(run);
        }
        catch (Exception ex)
        {
            Error("Could not start VAbk Studio:\n\n" + ex.Message);
            return 1;
        }
        return 0;
    }

    // Prefer uv on PATH, fall back to the default per-user install location.
    static string ResolveUv()
    {
        string path = Environment.GetEnvironmentVariable("PATH") ?? "";
        foreach (string dir in path.Split(';'))
        {
            if (dir.Length == 0) continue;
            try
            {
                string cand = Path.Combine(dir.Trim(), "uv.exe");
                if (File.Exists(cand)) return cand;
            }
            catch { /* ignore malformed PATH entries */ }
        }

        string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        string fallback = Path.Combine(home, ".local", "bin", "uv.exe");
        return File.Exists(fallback) ? fallback : null;
    }

    // Re-quote any pass-through args that contain spaces or quotes.
    static string JoinArgs(string[] args)
    {
        if (args == null || args.Length == 0) return "";
        var parts = new string[args.Length];
        for (int i = 0; i < args.Length; i++)
        {
            string a = args[i];
            parts[i] = (a.IndexOf(' ') >= 0 || a.IndexOf('"') >= 0)
                ? "\"" + a.Replace("\"", "\\\"") + "\""
                : a;
        }
        return string.Join(" ", parts);
    }

    static void Error(string msg)
    {
        MessageBox.Show(msg, "VAbk Studio Launcher", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
