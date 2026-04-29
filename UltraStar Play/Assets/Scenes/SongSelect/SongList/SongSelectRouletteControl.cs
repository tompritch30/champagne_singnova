using System;
using System.Collections.Generic;
using System.Linq;
using UniInject;
using UniRx;
using UnityEngine;
using UnityEngine.UIElements;

#pragma warning disable CS0649

/**
 * Drives the song list as a virtualized vertical ListView.
 *
 * Class name preserved (originally "SongRouletteControl") so the existing
 * Inspector binding on SongSelectSceneControlsContainer.prefab and the
 * 19 dependent files keep working unchanged. The "wheel" is gone.
 */
public class SongRouletteControl : MonoBehaviour, INeedInjection
{
    public const float RowHeight = 56f;

    public VisualTreeAsset songEntryUi;

    [Inject]
    private Injector injector;

    [Inject]
    private SongSelectSongPreviewControl songPreviewControl;

    [Inject]
    private PlaylistManager playlistManager;

    [Inject]
    private Settings settings;

    [Inject]
    private NonPersistentSettings nonPersistentSettings;

    [Inject]
    private SongSearchControl songSearchControl;

    [Inject(UxmlName = R.UxmlNames.songListView)]
    private ListView songListView;

    private List<SongSelectEntry> entries = new();
    public IReadOnlyList<SongSelectEntry> Entries => entries;

    public IReactiveProperty<SongSelectEntrySelection> Selection { get; private set; } = new ReactiveProperty<SongSelectEntrySelection>();

    private readonly Subject<SongSelectEntrySelection> selectionClickedEventStream = new();
    public IObservable<SongSelectEntrySelection> SelectionClickedEventStream => selectionClickedEventStream;

    private readonly Subject<List<SongSelectEntry>> entryListChangedEventStream = new();
    public IObservable<List<SongSelectEntry>> EntryListChangedEventStream => entryListChangedEventStream;

    private readonly Subject<SongSelectEntry> submitEventStream = new();
    public IObservable<SongSelectEntry> SubmitEventStream => submitEventStream;

    public int SelectedEntryIndex
    {
        get
        {
            return Selection.Value.Entry != null
                ? Selection.Value.Index
                : -1;
        }
    }

    public SongSelectEntry SelectedEntry
    {
        get
        {
            return Selection.Value.Entry;
        }
    }

    private readonly List<SongSelectEntryControl> entryControls = new();
    public IReadOnlyList<SongSelectEntryControl> EntryControls => entryControls;
    public SongSelectEntryControl SelectedEntryControl => entryControls
        .FirstOrDefault(it => it.SongSelectEntry == SelectedEntry);

    private readonly Subject<SongSelectEntryControl> createdSongSelectEntryControlEventStream = new();
    public IObservable<SongSelectEntryControl> CreatedSongSelectEntryControlEventStream => createdSongSelectEntryControlEventStream;
    public IReadOnlyList<SongSelectSongEntry> SongEntries => entries.OfType<SongSelectSongEntry>().ToList();

    private bool isInitialized;
    private float lastPlaySongSelectSoundEffectTimeInSeconds;
    private SongSelectEntry initiallySelectedEntry;

    private void Start()
    {
        using IDisposable d = ProfileMarkerUtils.Auto("SongRouletteControl.Start");

        songListView.fixedItemHeight = RowHeight;
        songListView.selectionType = SelectionType.Single;
        songListView.virtualizationMethod = CollectionVirtualizationMethod.FixedHeight;

        songListView.RegisterCallback<KeyDownEvent>(evt =>
        {
            if (entries.IsNullOrEmpty())
            {
                return;
            }

            if ((evt.keyCode == KeyCode.End && Selection.Value.Index == entries.Count - 1)
                || (evt.keyCode == KeyCode.Home && Selection.Value.Index == 0))
            {
                evt.StopImmediatePropagation();
            }
        }, TrickleDown.TrickleDown);

        songListView.RegisterCallback<NavigationSubmitEvent>(_ =>
        {
            if (songSearchControl.IsSearching)
            {
                songSearchControl.SubmitSearch();
                return;
            }

            if (SelectedEntry != null)
            {
                submitEventStream.OnNext(SelectedEntry);
            }
        }, TrickleDown.TrickleDown);

        ScrollView listViewScrollView = songListView.Q<ScrollView>();
        if (listViewScrollView != null)
        {
            settings.ObserveEveryValueChanged(_ => settings.ShowScrollBarInSongSelect)
                .Subscribe(newValue =>
                {
                    listViewScrollView.verticalScrollerVisibility = newValue
                        ? ScrollerVisibility.Auto
                        : ScrollerVisibility.Hidden;
                })
                .AddTo(gameObject);
            listViewScrollView.horizontalScrollerVisibility = ScrollerVisibility.Hidden;
        }

        songListView.makeItem = OnMakeItem;
        songListView.bindItem = OnBindItem;
        songListView.unbindItem = OnUnbindItem;
        songListView.selectedIndicesChanged += OnSongListViewSelectionIndexChanged;

        InitSelectionSoundEffect();

        isInitialized = true;

        if (!entries.IsNullOrEmpty())
        {
            SetEntries(entries);
        }

        SelectInitialEntry();
    }

    private void SelectInitialEntry()
    {
        if (initiallySelectedEntry == null
            || entries.IsNullOrEmpty()
            || !entries.Contains(initiallySelectedEntry))
        {
            return;
        }

        if (VisualElementUtils.HasGeometry(songListView))
        {
            DoSelectInitialEntry();
        }
        else
        {
            songListView.RegisterHasGeometryCallbackOneShot(_ => DoSelectInitialEntry());
        }
    }

    private void DoSelectInitialEntry()
    {
        SelectEntry(initiallySelectedEntry);
    }

    private void OnUnbindItem(VisualElement element, int index)
    {
        SongSelectEntry entry = element.userData as SongSelectEntry;
        if (entry == null)
        {
            return;
        }
        element.userData = null;

        SongSelectEntryControl songSelectEntryControl = entryControls.FirstOrDefault(it => it.SongSelectEntry == entry);
        if (songSelectEntryControl != null)
        {
            songSelectEntryControl.Dispose();
            entryControls.Remove(songSelectEntryControl);
        }
    }

    private void OnBindItem(VisualElement element, int index)
    {
        if (index < 0 || index >= entries.Count)
        {
            element.HideByVisibility();
            return;
        }
        element.ShowByVisibility();

        SongSelectEntry entry = entries[index];
        element.userData = entry;
        CreateEntryControl(entry, element);

        bool isSelected = songListView.selectedIndex == index;
        ApplyThemeStyleUtils.SetListViewItemActive(songListView, element, isSelected);
    }

    private VisualElement OnMakeItem()
    {
        VisualElement songEntryVisualElement = songEntryUi.CloneTree().Children().FirstOrDefault();
        return songEntryVisualElement;
    }

    private void InitSelectionSoundEffect()
    {
        Selection.Subscribe(_ => PlaySelectionSoundEffect());
    }

    private void PlaySelectionSoundEffect()
    {
        if (Time.time < lastPlaySongSelectSoundEffectTimeInSeconds + 0.1f)
        {
            return;
        }

        lastPlaySongSelectSoundEffectTimeInSeconds = Time.time;
        SfxManager.PlaySongSelectSound();
    }

    private void Update()
    {
        entryControls.ForEach(entryControl => entryControl.Update());
    }

    private void OnSongListViewSelectionIndexChanged(IEnumerable<int> selectedIndexes)
    {
        if (InputUtils.IsKeyboardShiftPressed())
        {
            return;
        }

        int selectedIndex = selectedIndexes.FirstOrDefault();
        if (selectedIndex < 0 || selectedIndex >= entries.Count)
        {
            return;
        }
        SongSelectEntry selectedEntry = entries.ElementAtOrDefault(selectedIndex);
        SelectEntry(selectedEntry);
    }

    private void CreateEntryControl(SongSelectEntry entry, VisualElement songEntryVisualElement)
    {
        SongSelectEntryControl item = injector
            .WithRootVisualElement(songEntryVisualElement)
            .CreateAndInject<SongSelectEntryControl>();
        item.SongSelectEntry = entry;

        if (entry is SongSelectSongEntry songEntry)
        {
            item.Name = songEntry.SongMeta.GetArtistDashTitle();
        }
        else if (entry is SongSelectFolderEntry folderEntry)
        {
            item.Name = folderEntry.DirectoryInfo.Name;
        }

        item.ClickEventStream.Subscribe(_ => OnEntryClicked(entry));

        entryControls.Add(item);

        createdSongSelectEntryControlEventStream.OnNext(item);
    }

    public void SetEntries(IReadOnlyCollection<SongSelectEntry> newEntries)
    {
        using IDisposable d = ProfileMarkerUtils.Auto("SongRouletteControl.SetEntries");

        SongSelectEntry lastSelectedEntry = SelectedEntry;
        entries = new List<SongSelectEntry>(newEntries);

        if (!isInitialized)
        {
            return;
        }

        RestoreLastSelection(lastSelectedEntry);

        if (!VisualElementUtils.HasGeometry(songListView))
        {
            songListView.RegisterCallbackOneShot<GeometryChangedEvent>(_ => UpdateListViewItems());
        }
        else
        {
            UpdateListViewItems();
        }

        entryListChangedEventStream.OnNext(entries);
    }

    private void RestoreLastSelection(SongSelectEntry lastSelectedEntry)
    {
        if (entries.IsNullOrEmpty())
        {
            Selection.Value = new SongSelectEntrySelection(null, -1, 0);
            return;
        }

        int restoredSelectedIndex = entries.IndexOf(lastSelectedEntry);
        if (restoredSelectedIndex >= 0)
        {
            Selection.Value = new SongSelectEntrySelection(entries[restoredSelectedIndex], restoredSelectedIndex, entries.Count);
            return;
        }

        Selection.Value = new SongSelectEntrySelection(entries.FirstOrDefault(), 0, entries.Count);
    }

    private void UpdateListViewItems()
    {
        songListView.itemsSource = entries;
        songListView.RefreshItems();
        if (SelectedEntry != null)
        {
            SetSelectionAndScrollToIndex(SelectedEntryIndex);
        }
    }

    private void SetSelectionAndScrollToIndex(int songIndex)
    {
        if (songIndex < 0 || songIndex >= entries.Count)
        {
            return;
        }
        if (songListView.selectedIndex != songIndex)
        {
            songListView.SetSelection(songIndex);
        }
        songListView.ScrollToItem(songIndex);
    }

    public SongSelectEntry GetEntryBySongMeta(SongMeta songMeta)
    {
        return entries.FirstOrDefault(entry => entry is SongSelectSongEntry songEntry
                                               && songEntry.SongMeta == songMeta);
    }

    public int GetEntryIndexBySongMeta(SongMeta songMeta)
    {
        SongSelectEntry matchingEntry = GetEntryBySongMeta(songMeta);
        return matchingEntry == null ? -1 : entries.IndexOf(matchingEntry);
    }

    public void SelectEntryBySongMeta(SongMeta songMeta)
    {
        SelectEntry(GetEntryBySongMeta(songMeta));
    }

    /** No-op kept for API compatibility (wheel transition is gone). */
    public void FinishTransition() { }

    /** No-op kept for API compatibility. */
    public void StartTransition() { }

    public void SelectEntry(SongSelectEntry entry)
    {
        if (entry == null)
        {
            return;
        }

        if (!isInitialized)
        {
            initiallySelectedEntry = entry;
            return;
        }

        int index = entries.IndexOf(entry);
        if (index < 0)
        {
            return;
        }

        if (SelectedEntry == entry && SelectedEntryIndex == index)
        {
            return;
        }

        SetSelectionAndScrollToIndex(index);
        UpdateSongSelectDirectoryPathToLastSelection(entry);
        Selection.Value = new SongSelectEntrySelection(entry, index, entries.Count);
    }

    private void UpdateSongSelectDirectoryPathToLastSelection(SongSelectEntry entry)
    {
        if (nonPersistentSettings.SongSelectDirectoryInfo == null)
        {
            return;
        }

        string path = GetPath(entry);
        if (path.IsNullOrEmpty())
        {
            return;
        }

        nonPersistentSettings.SongSelectDirectoryPathToLastSelection[nonPersistentSettings.SongSelectDirectoryInfo.FullName] = path;
    }

    private string GetPath(SongSelectEntry entry)
    {
        if (entry is SongSelectFolderEntry folderEntry)
        {
            return folderEntry.DirectoryInfo.FullName;
        }
        else if (entry is SongSelectSongEntry songEntry)
        {
            return songEntry.SongMeta.FileInfo?.FullName;
        }
        return null;
    }

    public void SelectEntryByIndex(int index, bool wrapAround = true)
    {
        if (!wrapAround
            && (index < 0 || entries.Count <= index))
        {
            return;
        }

        SongSelectEntry nextEntry = GetEntryAtIndex(index);
        SelectEntry(nextEntry);
    }

    public void SelectEntryByPath(string path)
    {
        SelectEntry(Find(entry => (entry is SongSelectFolderEntry folderEntry
                                   && folderEntry.DirectoryInfo.FullName == path)
                                    || (entry is SongSelectSongEntry songEntry
                                        && songEntry.SongMeta.FileInfo?.FullName == path)));
    }

    public SongSelectEntry Find(Predicate<SongSelectEntry> predicate)
    {
        return entries.Find(predicate);
    }

    public SongSelectEntry FindLast(Predicate<SongSelectEntry> predicate)
    {
        return entries.FindLast(predicate);
    }

    public void SelectNextEntry()
    {
        int nextIndex = SelectedEntryIndex < 0 ? 0 : SelectedEntryIndex + 1;
        SelectEntryByIndex(nextIndex);
    }

    public void SelectPreviousEntry()
    {
        int nextIndex = SelectedEntryIndex < 0 ? 0 : SelectedEntryIndex - 1;
        SelectEntryByIndex(nextIndex);
    }

    public void SelectVeryLastEntry()
    {
        SelectEntryByIndex(entries.Count - 1);
    }

    public void SelectVeryFirstEntry()
    {
        SelectEntryByIndex(0);
    }

    public SongSelectEntry GetEntryAtIndex(int index)
    {
        if (entries.Count == 0)
        {
            return null;
        }
        int wrappedIndex = (index < 0) ? index + entries.Count : index;
        int wrappedIndexModulo = wrappedIndex % entries.Count;
        if (wrappedIndexModulo < 0)
        {
            wrappedIndexModulo = 0;
        }
        return entries[wrappedIndexModulo];
    }

    private void OnEntryClicked(SongSelectEntry entry)
    {
        if (SelectedEntry != null
            && SelectedEntry == entry
            && Selection.Value.SelectionTime.AddMilliseconds(100) < DateTime.Now)
        {
            selectionClickedEventStream.OnNext(Selection.Value);
        }
        else
        {
            SelectEntry(entry);
        }
    }

    public void Focus()
    {
        songListView.Focus();
    }

    public void OpenSelectedEntryContextMenu()
    {
        if (SelectedEntryControl == null)
        {
            return;
        }

        SelectedEntryControl.OpenContextMenu();
    }
}
