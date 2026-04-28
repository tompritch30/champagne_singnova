using System;
using System.Collections.Generic;
using UniInject;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UIElements;

// Disable warning about fields that are never assigned, their values are injected.
#pragma warning disable CS0649

/// <summary>
/// Shows the USDB song catalog (songs.db) and lets the user trigger on-demand
/// downloads via the companion Python API server (api_server.py, port 5123).
/// </summary>
public class UsdbBrowserControl : INeedInjection, IInjectionFinishedListener
{
    private const string ApiBase = "http://127.0.0.1:5123";

    [Inject(UxmlName = "usdbBrowserOverlay")]
    private VisualElement usdbBrowserOverlay;

    [Inject(UxmlName = "usdbBrowserCloseButton")]
    private Button usdbBrowserCloseButton;

    [Inject(UxmlName = "usdbBrowserSearchField")]
    private TextField usdbBrowserSearchField;

    [Inject(UxmlName = "usdbBrowserSearchButton")]
    private Button usdbBrowserSearchButton;

    [Inject(UxmlName = "usdbBrowserStatusLabel")]
    private Label usdbBrowserStatusLabel;

    [Inject(UxmlName = "usdbBrowserScrollView")]
    private ScrollView usdbBrowserScrollView;

    [Inject(UxmlName = "usdbBrowserCatalogSizeLabel")]
    private Label usdbBrowserCatalogSizeLabel;

    [Inject]
    private SongMetaManager songMetaManager;

    // usdb_id -> download row VisualElement (to update progress label in-place)
    private readonly Dictionary<string, VisualElement> rowByUsdbId = new();
    // usdb_ids currently being polled for status
    private readonly HashSet<string> pollingIds = new();

    public void OnInjectionFinished()
    {
        usdbBrowserOverlay.HideByDisplay();

        usdbBrowserCloseButton.RegisterCallbackButtonTriggered(_ => Hide());

        usdbBrowserSearchButton.RegisterCallbackButtonTriggered(async _ =>
        {
            await SearchAsync(usdbBrowserSearchField.value.Trim());
        });

        usdbBrowserSearchField.RegisterCallback<NavigationSubmitEvent>(async _ =>
        {
            await SearchAsync(usdbBrowserSearchField.value.Trim());
        });
    }

    public void Show()
    {
        usdbBrowserOverlay.ShowByDisplay();
        usdbBrowserSearchField.Focus();
        ShowInitialResults();
    }

    private async void ShowInitialResults() => await SearchAsync("");

    public void Hide()
    {
        usdbBrowserOverlay.HideByDisplay();
    }

    // -------------------------------------------------------------------------

    private async Awaitable SearchAsync(string query)
    {
        usdbBrowserStatusLabel.text = "Searching...";
        usdbBrowserScrollView.Clear();
        rowByUsdbId.Clear();

        string url = string.IsNullOrEmpty(query)
            ? $"{ApiBase}/api/search?limit=40"
            : $"{ApiBase}/api/search?q={Uri.EscapeDataString(query)}&limit=40";

        string json;
        try
        {
            json = await GetJsonAsync(url);
        }
        catch (Exception ex)
        {
            usdbBrowserStatusLabel.text = $"Cannot reach API server - start api_server.py first. ({ex.Message})";
            return;
        }

        UsdbSearchResponse response;
        try
        {
            response = JsonUtility.FromJson<UsdbSearchResponse>(json);
        }
        catch (Exception ex)
        {
            usdbBrowserStatusLabel.text = $"Bad response: {ex.Message}";
            return;
        }

        if (response?.results == null || response.results.Count == 0)
        {
            usdbBrowserStatusLabel.text = string.IsNullOrEmpty(query)
                ? "No songs in database."
                : $"No results for: {query}";
            return;
        }

        usdbBrowserStatusLabel.text = string.IsNullOrEmpty(query)
            ? $"Showing top {response.results.Count} songs. Type to search."
            : $"{response.results.Count} result(s) for: {query}";

        foreach (UsdbSearchResult song in response.results)
        {
            VisualElement row = CreateRow(song);
            usdbBrowserScrollView.Add(row);
            rowByUsdbId[song.usdb_id] = row;
        }
    }

    private VisualElement CreateRow(UsdbSearchResult song)
    {
        VisualElement row = new();
        row.style.flexDirection = FlexDirection.Row;
        row.style.alignItems = Align.Center;
        row.style.paddingLeft = row.style.paddingRight = 8;
        row.style.paddingTop = row.style.paddingBottom = 5;
        row.style.borderBottomWidth = 1;
        row.style.borderBottomColor = new Color(1, 1, 1, 0.05f);
        row.name = $"usdb-row-{song.usdb_id}";

        Label artistLabel = new(song.artist);
        artistLabel.style.width = new Length(28, LengthUnit.Percent);
        artistLabel.style.overflow = Overflow.Hidden;
        row.Add(artistLabel);

        Label titleLabel = new(song.title);
        titleLabel.style.flexGrow = 1;
        titleLabel.style.overflow = Overflow.Hidden;
        row.Add(titleLabel);

        Label yearLabel = new(song.year);
        yearLabel.style.width = 50;
        yearLabel.style.unityTextAlign = TextAnchor.MiddleRight;
        yearLabel.AddToClassList("secondaryFontColor");
        yearLabel.AddToClassList("smallFont");
        row.Add(yearLabel);

        VisualElement actionArea = new();
        actionArea.style.width = 120;
        actionArea.style.alignItems = Align.FlexEnd;
        row.Add(actionArea);

        SetRowActionArea(actionArea, song);
        return row;
    }

    private void SetRowActionArea(VisualElement actionArea, UsdbSearchResult song)
    {
        actionArea.Clear();

        bool isComplete = song.status == "complete";

        if (isComplete)
        {
            Label doneLabel = new("Ready");
            doneLabel.AddToClassList("smallFont");
            doneLabel.style.color = new Color(0.4f, 0.9f, 0.4f);
            actionArea.Add(doneLabel);
        }
        else
        {
            Button downloadBtn = new();
            downloadBtn.text = "Download";
            downloadBtn.AddToClassList("smallFont");
            downloadBtn.style.height = 22;
            downloadBtn.style.paddingLeft = downloadBtn.style.paddingRight = 8;
            downloadBtn.RegisterCallbackButtonTriggered(async _ =>
            {
                downloadBtn.SetEnabled(false);
                downloadBtn.text = "Starting...";
                await StartDownloadAsync(song.usdb_id, actionArea, song);
            });
            actionArea.Add(downloadBtn);
        }
    }

    private async Awaitable StartDownloadAsync(string usdbId, VisualElement actionArea, UsdbSearchResult song)
    {
        string url = $"{ApiBase}/api/download";
        string body = $"{{\"usdb_id\":\"{usdbId}\"}}";

        string json;
        try
        {
            json = await PostJsonAsync(url, body);
        }
        catch (Exception ex)
        {
            SetActionAreaError(actionArea, $"Error: {ex.Message}");
            return;
        }

        UsdbDownloadResponse resp;
        try
        {
            resp = JsonUtility.FromJson<UsdbDownloadResponse>(json);
        }
        catch
        {
            SetActionAreaError(actionArea, "Bad response");
            return;
        }

        if (!resp.started && resp.reason != null && resp.reason != "already running")
        {
            SetActionAreaError(actionArea, resp.reason);
            return;
        }

        // Show progress bar and begin polling
        ShowProgressInRow(actionArea, "Downloading...", 0f);
        if (!pollingIds.Contains(usdbId))
        {
            pollingIds.Add(usdbId);
            await PollStatusAsync(usdbId, actionArea, song);
        }
    }

    private async Awaitable PollStatusAsync(string usdbId, VisualElement actionArea, UsdbSearchResult song)
    {
        while (true)
        {
            await Awaitable.WaitForSecondsAsync(2.5f);

            // Panel may have been closed/rebuilt
            if (!usdbBrowserOverlay.IsVisibleByDisplay())
            {
                pollingIds.Remove(usdbId);
                return;
            }

            string json;
            try
            {
                json = await GetJsonAsync($"{ApiBase}/api/status?usdb_id={usdbId}");
            }
            catch
            {
                pollingIds.Remove(usdbId);
                return;
            }

            UsdbStatusResponse status;
            try
            {
                status = JsonUtility.FromJson<UsdbStatusResponse>(json);
            }
            catch
            {
                pollingIds.Remove(usdbId);
                return;
            }

            if (status.status == "complete")
            {
                pollingIds.Remove(usdbId);
                ShowProgressInRow(actionArea, null, 1f);
                await Awaitable.WaitForSecondsAsync(0.5f);
                // Replace with "Ready" label
                actionArea.Clear();
                Label doneLabel = new("Ready");
                doneLabel.AddToClassList("smallFont");
                doneLabel.style.color = new Color(0.4f, 0.9f, 0.4f);
                actionArea.Add(doneLabel);
                // Trigger game rescan so the new song appears in SongSelect
                songMetaManager.RescanSongs();
                return;
            }
            else if (status.status == "failed")
            {
                pollingIds.Remove(usdbId);
                SetActionAreaError(actionArea, "Failed");
                return;
            }
            else if (!status.running && status.status != "indexed")
            {
                // Process exited but status wasn't updated to complete/failed — treat as failed
                pollingIds.Remove(usdbId);
                SetActionAreaError(actionArea, "Failed");
                return;
            }

            // Still running — animate label
            string dots = new string('.', (int)(Time.time * 1.5f) % 4);
            ShowProgressInRow(actionArea, $"Downloading{dots}", -1f);
        }
    }

    private static void ShowProgressInRow(VisualElement actionArea, string labelText, float fillRatio)
    {
        // Re-use or create progress container
        VisualElement container = actionArea.Q("progressContainer");
        if (container == null)
        {
            actionArea.Clear();
            container = new VisualElement();
            container.name = "progressContainer";
            container.style.alignItems = Align.FlexEnd;
            container.style.width = new Length(100, LengthUnit.Percent);
            actionArea.Add(container);
        }

        Label lbl = container.Q<Label>("progressLabel");
        if (lbl == null)
        {
            lbl = new Label();
            lbl.name = "progressLabel";
            lbl.AddToClassList("smallFont");
            lbl.style.color = new Color(0.9f, 0.75f, 0.2f);
            container.Add(lbl);
        }

        VisualElement bar = container.Q("progressBar");
        if (bar == null)
        {
            VisualElement track = new();
            track.name = "progressTrack";
            track.style.width = new Length(100, LengthUnit.Percent);
            track.style.height = 3;
            track.style.backgroundColor = new Color(1, 1, 1, 0.15f);
            track.style.marginTop = 2;
            container.Add(track);

            bar = new VisualElement();
            bar.name = "progressBar";
            bar.style.height = 3;
            bar.style.backgroundColor = new Color(0.9f, 0.75f, 0.2f);
            track.Add(bar);
        }

        if (labelText != null)
        {
            lbl.text = labelText;
        }

        if (fillRatio >= 0f)
        {
            bar.style.width = new Length(Mathf.Clamp01(fillRatio) * 100f, LengthUnit.Percent);
        }
        else
        {
            // Indeterminate — animate using a sine wave on width
            float t = Mathf.Abs(Mathf.Sin(Time.time * 1.2f));
            bar.style.width = new Length(30f + t * 60f, LengthUnit.Percent);
        }
    }

    private static void SetActionAreaError(VisualElement actionArea, string msg)
    {
        actionArea.Clear();
        Label errLabel = new(msg);
        errLabel.AddToClassList("smallFont");
        errLabel.style.color = new Color(1f, 0.4f, 0.4f);
        actionArea.Add(errLabel);
    }

    // -------------------------------------------------------------------------
    // HTTP helpers
    // -------------------------------------------------------------------------

    private static async Awaitable<string> GetJsonAsync(string url)
    {
        using UnityWebRequest req = UnityWebRequest.Get(url);
        req.timeout = 8;
        await WebRequestUtils.SendWebRequestAsync(req);
        if (req.result != UnityWebRequest.Result.Success)
        {
            throw new Exception(req.error);
        }
        return req.downloadHandler.text;
    }

    private static async Awaitable<string> PostJsonAsync(string url, string jsonBody)
    {
        byte[] bodyBytes = System.Text.Encoding.UTF8.GetBytes(jsonBody);
        using UnityWebRequest req = new(url, "POST");
        req.uploadHandler = new UploadHandlerRaw(bodyBytes);
        req.downloadHandler = new DownloadHandlerBuffer();
        req.SetRequestHeader("Content-Type", "application/json");
        req.timeout = 8;
        await WebRequestUtils.SendWebRequestAsync(req);
        if (req.result != UnityWebRequest.Result.Success)
        {
            throw new Exception(req.error);
        }
        return req.downloadHandler.text;
    }

    // -------------------------------------------------------------------------
    // JSON DTOs (JsonUtility requires [Serializable])
    // -------------------------------------------------------------------------

    [Serializable]
    private class UsdbSearchResult
    {
        public string usdb_id;
        public string artist;
        public string title;
        public string year;
        public string language;
        public string genre;
        public string status;
    }

    [Serializable]
    private class UsdbSearchResponse
    {
        public List<UsdbSearchResult> results;
        public int count;
    }

    [Serializable]
    private class UsdbDownloadResponse
    {
        public bool started;
        public string reason;
        public string usdb_id;
    }

    [Serializable]
    private class UsdbStatusResponse
    {
        public string status;
        public bool running;
        public string artist;
        public string title;
        public string folder_path;
    }
}
