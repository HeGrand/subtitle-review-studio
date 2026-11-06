using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class StudioLauncher
{
    [STAThread]
    private static int Main()
    {
        try
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string desktop = Path.Combine(root, "desktop");
            string electron = Path.Combine(root, @".runtime\desktop\electron.exe");
            if (!File.Exists(electron))
                electron = Path.Combine(root, @"node_modules\electron\dist\electron.exe");
            if (!File.Exists(electron) || !File.Exists(Path.Combine(desktop, "main.cjs")))
                throw new FileNotFoundException("桌面组件不完整，请将整个应用文件夹完整解压后再启动。");

            string python = Path.Combine(root, @".runtime\python\python.exe");
            bool portable = File.Exists(Path.Combine(root, "portable.json"));
            if (portable && !File.Exists(python))
                throw new FileNotFoundException("便携版的 Python 运行组件缺失，请完整解压应用文件夹。");

            var start = new ProcessStartInfo(electron, "\"" + desktop + "\"");
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.EnvironmentVariables["SUBTITLE_APP_ROOT"] = root;
            if (File.Exists(python))
                start.EnvironmentVariables["SUBTITLE_PYTHON"] = python;
            if (portable)
            {
                start.EnvironmentVariables["HF_HUB_OFFLINE"] = "1";
                start.EnvironmentVariables["HF_HUB_DISABLE_TELEMETRY"] = "1";
                start.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
            }
            using (Process.Start(start)) { }
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "字幕工作室未启动", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
