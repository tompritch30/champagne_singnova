using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using UnityEngine;
using Debug = UnityEngine.Debug;

public static class SongIndexCache
{
    private const int SchemaVersion = 2;
    private const string CacheFileName = "song_index.cache.json";

    [Serializable]
    private class CacheFile
    {
        public int version;
        public string indexedAt;
        public List<CacheEntry> entries = new();
    }

    [Serializable]
    public class CacheEntry
    {
        public string txtPath;
        public long mtimeUtcTicks;
    }

    // Must be called from main thread (Application.persistentDataPath is main-thread-only).
    public static string GetCachePath()
    {
        return Path.Combine(Application.persistentDataPath, CacheFileName);
    }

    public static List<CacheEntry> Load(string path)
    {
        if (!File.Exists(path))
        {
            return new List<CacheEntry>();
        }

        try
        {
            string json = File.ReadAllText(path);
            CacheFile cache = JsonConvert.DeserializeObject<CacheFile>(json);
            if (cache == null || cache.version != SchemaVersion)
            {
                Debug.Log($"SongIndexCache: stale schema (got {cache?.version}, want {SchemaVersion}) — ignoring");
                return new List<CacheEntry>();
            }
            return cache.entries ?? new List<CacheEntry>();
        }
        catch (Exception ex)
        {
            Debug.LogWarning($"SongIndexCache: failed to load — {ex.Message}");
            return new List<CacheEntry>();
        }
    }

    public static void Save(string path, IReadOnlyCollection<CacheEntry> entries)
    {
        string tmpPath = path + ".tmp";
        try
        {
            CacheFile cache = new CacheFile
            {
                version = SchemaVersion,
                indexedAt = DateTime.UtcNow.ToString("o"),
                entries = entries.ToList(),
            };
            string json = JsonConvert.SerializeObject(cache);
            File.WriteAllText(tmpPath, json);
            if (File.Exists(path))
            {
                File.Replace(tmpPath, path, null);
            }
            else
            {
                File.Move(tmpPath, path);
            }
            Debug.Log($"SongIndexCache: saved {entries.Count} entries to '{path}'");
        }
        catch (Exception ex)
        {
            Debug.LogWarning($"SongIndexCache: failed to save — {ex.Message}");
            try { if (File.Exists(tmpPath)) File.Delete(tmpPath); } catch { }
        }
    }

    public static void Clear(string path)
    {
        try
        {
            if (File.Exists(path))
            {
                File.Delete(path);
                Debug.Log($"SongIndexCache: cleared '{path}'");
            }
        }
        catch (Exception ex)
        {
            Debug.LogWarning($"SongIndexCache: failed to clear — {ex.Message}");
        }
    }

    public static CacheEntry CreateFromTxtFile(string txtPath)
    {
        try
        {
            FileInfo fi = new FileInfo(txtPath);
            return new CacheEntry
            {
                txtPath = fi.FullName,
                mtimeUtcTicks = fi.LastWriteTimeUtc.Ticks,
            };
        }
        catch
        {
            return new CacheEntry { txtPath = txtPath, mtimeUtcTicks = 0 };
        }
    }

    public static bool IsCacheEntryFresh(CacheEntry entry)
    {
        if (entry == null || string.IsNullOrEmpty(entry.txtPath))
        {
            return false;
        }
        try
        {
            FileInfo fi = new FileInfo(entry.txtPath);
            return fi.Exists && fi.LastWriteTimeUtc.Ticks == entry.mtimeUtcTicks;
        }
        catch
        {
            return false;
        }
    }
}
