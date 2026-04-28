using System;
using System.IO;
using System.Net.Sockets;
using UnityEngine;

public static class AppBootstrap
{
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
    private static void Init()
    {
        if (Application.isEditor)
            return;

        // Application.dataPath in a build = "<exe_dir>/<ProductName>_Data"
        string gameDir = Path.GetDirectoryName(Application.dataPath);

        SetupSongFileCache(gameDir);
        StartApiServer(gameDir);
    }

    private static void SetupSongFileCache(string gameDir)
    {
        try
        {
            string downloadsDir = Path.Combine(gameDir, "downloads");
            string modSettingsDir = Path.Combine(
                Application.persistentDataPath,
                "ModsPersistentData", "SongFileCache");
            Directory.CreateDirectory(modSettingsDir);

            string settingsPath = Path.Combine(modSettingsDir, "modsettings.json");
            string escaped = downloadsDir.Replace("\\", "\\\\");
            File.WriteAllText(settingsPath,
                $"{{\"songFolder\":\"{escaped}\",\"cacheFileContent\":false}}");
        }
        catch (Exception ex)
        {
            Debug.LogWarning($"[AppBootstrap] SongFileCache setup failed: {ex.Message}");
        }
    }

    private static void StartApiServer(string gameDir)
    {
        string apiExe = Path.Combine(gameDir, "api_server.exe");
        if (!File.Exists(apiExe))
        {
            Debug.LogWarning("[AppBootstrap] api_server.exe not found next to game exe.");
            return;
        }

        if (IsPortInUse(5123))
        {
            Debug.Log("[AppBootstrap] api_server already running on port 5123.");
            return;
        }

        try
        {
            var proc = new System.Diagnostics.Process();
            proc.StartInfo = new System.Diagnostics.ProcessStartInfo
            {
                FileName = apiExe,
                WorkingDirectory = gameDir,
                CreateNoWindow = true,
                UseShellExecute = false,
            };
            proc.Start();
            Debug.Log("[AppBootstrap] Started api_server.exe");
        }
        catch (Exception ex)
        {
            Debug.LogWarning($"[AppBootstrap] Failed to start api_server.exe: {ex.Message}");
        }
    }

    private static bool IsPortInUse(int port)
    {
        try
        {
            using var tcp = new TcpClient();
            tcp.Connect("127.0.0.1", port);
            return true;
        }
        catch
        {
            return false;
        }
    }
}
