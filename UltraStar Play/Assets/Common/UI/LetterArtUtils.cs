using UnityEngine;
using UnityEngine.UIElements;

public static class LetterArtUtils
{
    private static readonly (Color32 bg, Color32 fg)[] LetterPalette = new (Color32 bg, Color32 fg)[]
    {
        (new Color32(0x83, 0x18, 0x43, 0xFF), new Color32(0xFB, 0xCF, 0xE8, 0xFF)),
        (new Color32(0x1E, 0x3A, 0x8A, 0xFF), new Color32(0xBF, 0xDB, 0xFE, 0xFF)),
        (new Color32(0x14, 0x53, 0x2D, 0xFF), new Color32(0xBB, 0xF7, 0xD0, 0xFF)),
        (new Color32(0x78, 0x35, 0x0F, 0xFF), new Color32(0xFD, 0xE6, 0x8A, 0xFF)),
        (new Color32(0x43, 0x38, 0xCA, 0xFF), new Color32(0xC7, 0xD2, 0xFE, 0xFF)),
        (new Color32(0x58, 0x1C, 0x87, 0xFF), new Color32(0xE9, 0xD5, 0xFF, 0xFF)),
        (new Color32(0x13, 0x4E, 0x4A, 0xFF), new Color32(0x99, 0xF6, 0xE4, 0xFF)),
        (new Color32(0x7F, 0x1D, 0x1D, 0xFF), new Color32(0xFE, 0xCA, 0xCA, 0xFF)),
    };

    public static int PaletteIndex(string key)
    {
        if (string.IsNullOrEmpty(key))
        {
            return 0;
        }
        int h = 0;
        foreach (char c in key)
        {
            h = (h * 31 + c) & 0x7FFFFFFF;
        }
        return h % LetterPalette.Length;
    }

    public static (Color32 bg, Color32 fg) GetColors(string key)
    {
        return LetterPalette[PaletteIndex(key)];
    }

    public static string GetLetter(SongMeta songMeta)
    {
        string source = songMeta != null && !string.IsNullOrEmpty(songMeta.Title)
            ? songMeta.Title
            : songMeta?.Artist;
        if (string.IsNullOrEmpty(source))
        {
            return "?";
        }
        return source.Substring(0, 1).ToUpperInvariant();
    }

    /**
     * Paint a solid color block + serif italic letter as cover fallback.
     * Clears any previous backgroundImage on imageOuter/imageInner so the NoCover.png is gone.
     */
    public static void ApplyLetterFallback(SongMeta songMeta, VisualElement imageOuter, VisualElement imageInner, Label letterLabel)
    {
        if (imageOuter == null)
        {
            return;
        }

        string key = songMeta?.Artist;
        if (string.IsNullOrEmpty(key))
        {
            key = songMeta?.Title ?? "";
        }
        (Color32 bg, Color32 fg) = GetColors(key);

        imageOuter.style.backgroundImage = new StyleBackground(StyleKeyword.None);
        imageOuter.style.unityBackgroundImageTintColor = new StyleColor(StyleKeyword.None);
        imageOuter.style.backgroundColor = new StyleColor((Color)bg);

        if (imageInner != null)
        {
            imageInner.style.backgroundImage = new StyleBackground(StyleKeyword.None);
            imageInner.style.unityBackgroundImageTintColor = new StyleColor(StyleKeyword.None);
            imageInner.style.backgroundColor = new StyleColor(new Color(0, 0, 0, 0));
        }

        if (letterLabel != null)
        {
            letterLabel.text = GetLetter(songMeta);
            letterLabel.style.color = new StyleColor((Color)fg);
            letterLabel.style.display = DisplayStyle.Flex;
        }
    }

    /**
     * Apply when a real cover sprite is loaded — hide the letter and let the cover show through.
     */
    public static void HideLetter(VisualElement imageOuter, Label letterLabel)
    {
        if (imageOuter != null)
        {
            imageOuter.style.backgroundColor = new StyleColor(new Color(0, 0, 0, 0));
        }
        if (letterLabel != null)
        {
            letterLabel.style.display = DisplayStyle.None;
        }
    }
}
