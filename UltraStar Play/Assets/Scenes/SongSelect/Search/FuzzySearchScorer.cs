using System;
using System.Collections.Generic;

/// <summary>
/// Scores a SongMeta against a search query for ranked fuzzy search.
///
/// Scoring per query word (each word scored independently, song score = average):
///   1.00  – field contains the word exactly (case/diacritic-insensitive)
///   0.90  – combined "artist title" string contains the word
///   0.85  – field starts with the word
///   0.45+ – trigram similarity >= 0.35 (catches 1–2 char typos; score = sim * 0.9)
///   0.00  – no match (song excluded from results)
///
/// Trigram similarity (Dice coefficient on character 3-grams):
///   sim = 2 * |A ∩ B| / (|A| + |B|)
/// Short words (< 3 chars) fall back to prefix/contains only.
/// </summary>
public static class FuzzySearchScorer
{
    // Minimum trigram similarity to count as a fuzzy match
    private const float TrigramThreshold = 0.35f;

    // ---------------------------------------------------------------------------
    // Public API
    // ---------------------------------------------------------------------------

    /// <summary>
    /// Returns a relevance score in [0, 1]. 0 means no match (exclude song).
    /// </summary>
    public static float ScoreSong(SongMeta song, string[] queryWords)
    {
        if (song == null || queryWords == null || queryWords.Length == 0)
        {
            return 0f;
        }

        string artistNorm = Norm(song.Artist);
        string titleNorm  = Norm(song.Title);
        string combined   = artistNorm + " " + titleNorm;

        float total = 0f;
        foreach (string word in queryWords)
        {
            if (word.Length == 0) continue;
            float wordScore = ScoreWord(word, artistNorm, titleNorm, combined);
            if (wordScore <= 0f) return 0f; // AND logic: all words must match
            total += wordScore;
        }
        return total / queryWords.Length;
    }

    // ---------------------------------------------------------------------------
    // Internal helpers
    // ---------------------------------------------------------------------------

    private static float ScoreWord(string word, string artist, string title, string combined)
    {
        // Exact contains in artist or title (highest priority)
        if (ContainsNorm(artist, word))   return 1.00f;
        if (ContainsNorm(title, word))    return 1.00f;

        // Combined "artist title" contains (slightly lower — cross-field match)
        if (ContainsNorm(combined, word)) return 0.90f;

        // Starts-with in artist or title
        if (StartsWithNorm(artist, word)) return 0.85f;
        if (StartsWithNorm(title, word))  return 0.85f;

        // Trigram fuzzy match (only useful for words >= 3 chars)
        if (word.Length >= 3)
        {
            float artistSim  = TrigramSim(word, artist);
            float titleSim   = TrigramSim(word, title);
            float bestSim    = Math.Max(artistSim, titleSim);
            if (bestSim >= TrigramThreshold)
            {
                return bestSim * 0.9f; // scale so fuzzy is always < exact match
            }
        }

        return 0f;
    }

    // ---------------------------------------------------------------------------
    // Normalization
    // ---------------------------------------------------------------------------

    private static string Norm(string s)
    {
        if (s == null) return "";
        return StringUtils.RemoveDiacritics(s).ToLowerInvariant();
    }

    private static bool ContainsNorm(string haystack, string needle)
    {
        return haystack.Contains(needle, StringComparison.Ordinal);
    }

    private static bool StartsWithNorm(string haystack, string needle)
    {
        return haystack.StartsWith(needle, StringComparison.Ordinal);
    }

    // ---------------------------------------------------------------------------
    // Trigram Dice-coefficient similarity
    // ---------------------------------------------------------------------------

    private static float TrigramSim(string a, string b)
    {
        if (a.Length < 3 || b.Length < 3) return 0f;

        HashSet<string> aTrigrams = GetTrigrams(a);
        HashSet<string> bTrigrams = GetTrigrams(b);

        int intersection = 0;
        foreach (string t in aTrigrams)
        {
            if (bTrigrams.Contains(t)) intersection++;
        }

        return 2f * intersection / (aTrigrams.Count + bTrigrams.Count);
    }

    private static HashSet<string> GetTrigrams(string s)
    {
        // Pad with spaces so edge characters get representation
        string padded = " " + s + " ";
        var trigrams = new HashSet<string>(padded.Length - 2);
        for (int i = 0; i < padded.Length - 2; i++)
        {
            trigrams.Add(padded.Substring(i, 3));
        }
        return trigrams;
    }
}
