from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from graphify.analyze import god_nodes, surprising_connections, suggest_questions
from graphify.build import build_from_json
from graphify.cluster import cluster, score_all
from graphify.detect import save_manifest
from graphify.export import to_html, to_json
from graphify.extract import extract
from graphify.report import generate


ROOT = Path('.')
OUTPUT_DIR = ROOT / 'graphify-out'

SOURCE_ROOTS = [
    ROOT / 'NukeBuild',
    ROOT / 'songs-pipeline' / 'scripts',
    ROOT / 'songs-pipeline' / 'README.md',
    ROOT / 'songs-pipeline' / 'config.json',
    ROOT / 'UltraStar Play' / 'Assets',
    ROOT / 'UltraStar Play' / 'Packages',
    ROOT / 'UltraStar Play' / 'ProjectSettings',
    ROOT / 'UltraStar Play' / 'Assembly-CSharp.ruleset',
    ROOT / 'UltraStar Play' / 'Common.csproj',
    ROOT / 'UltraStar Play' / 'Editor.csproj',
    ROOT / 'UltraStar Play' / 'Plugins.csproj',
    ROOT / 'UltraStar Play' / 'Scenes.csproj',
    ROOT / 'UltraStar Play' / 'UniRx.csproj',
    ROOT / 'UltraStar Play' / 'UnityStandaloneFileBrowser.csproj',
    ROOT / 'UltraStar Play' / 'UltraStar Play.sln',
    ROOT / 'UltraStar Play Companion' / 'Assets',
    ROOT / 'UltraStar Play Companion' / 'Packages',
    ROOT / 'UltraStar Play Companion' / 'ProjectSettings',
]

EXCLUDE_DIR_NAMES = {'Library', 'Temp', 'Logs', 'obj', 'bin', '.git'}
CODE_EXTS = {'.cs', '.py', '.ps1'}
DOC_EXTS = {'.md', '.txt', '.json', '.yml', '.yaml', '.csproj', '.sln', '.asmdef', '.ruleset'}
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}
VIDEO_EXTS = {'.mp4', '.mp3', '.wav', '.ogg'}

STOPWORDS = {
    'and', 'asset', 'assets', 'build', 'class', 'common', 'companion', 'data', 'editor', 'file',
    'game', 'helper', 'main', 'manager', 'nuke', 'pipeline', 'play', 'project', 'scene', 'settings',
    'song', 'songs', 'star', 'ultra', 'ultrastar', 'utility', 'utils', 'view', 'window',
}


def is_within_excluded_dir(path: Path) -> bool:
    return any(part in EXCLUDE_DIR_NAMES for part in path.parts)


def should_include(path: Path) -> bool:
    return path.is_file() and not is_within_excluded_dir(path)


def collect_paths() -> dict[str, list[Path]]:
    grouped: dict[str, list[Path]] = {'code': [], 'document': [], 'paper': [], 'image': [], 'video': []}
    for root in SOURCE_ROOTS:
        if not root.exists():
            continue
        if root.is_file():
            candidates = [root]
        else:
            candidates = [p for p in root.rglob('*') if should_include(p)]
        for path in candidates:
            suffix = path.suffix.lower()
            if suffix in CODE_EXTS:
                grouped['code'].append(path)
            elif suffix in DOC_EXTS:
                grouped['document'].append(path)
            elif suffix == '.pdf':
                grouped['paper'].append(path)
            elif suffix in IMAGE_EXTS:
                grouped['image'].append(path)
            elif suffix in VIDEO_EXTS:
                grouped['video'].append(path)
    for paths in grouped.values():
        paths.sort(key=lambda p: p.as_posix().lower())
    return grouped


def count_words(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        total += len(text.split())
    return total


def build_detection(grouped: dict[str, list[Path]]) -> dict:
    total_files = sum(len(paths) for paths in grouped.values())
    total_words = count_words(grouped['code']) + count_words(grouped['document'])
    files = {key: [path.as_posix() for path in paths] for key, paths in grouped.items()}
    return {
        'total_files': total_files,
        'total_words': total_words,
        'files': files,
        'skipped_sensitive': [],
    }


def extract_tokens(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r'[A-Za-z][A-Za-z0-9]+', text)]


def label_community(G, community_nodes: list[str]) -> str:
    token_counts: Counter[str] = Counter()
    directory_counts: Counter[str] = Counter()
    for node_id in community_nodes:
        node = G.nodes[node_id]
        token_counts.update(
            token for token in extract_tokens(str(node.get('label', node_id))) if token not in STOPWORDS and len(token) > 2
        )
        source_file = str(node.get('source_file', ''))
        if source_file:
            parent = Path(source_file).parent.name.lower()
            if parent and parent not in EXCLUDE_DIR_NAMES:
                directory_counts[parent] += 1
    if token_counts:
        top_tokens = [token for token, _ in token_counts.most_common(3)]
        return ' '.join(token.capitalize() for token in top_tokens[:2])
    if directory_counts:
        top_dirs = [directory for directory, _ in directory_counts.most_common(2)]
        return ' '.join(part.capitalize() for part in top_dirs)
    return 'Community'


def main() -> None:
    grouped = collect_paths()
    detection = build_detection(grouped)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_files = {key: [path.as_posix() for path in paths] for key, paths in grouped.items()}
    save_manifest(manifest_files)

    code_files = grouped['code']
    if not code_files:
        raise SystemExit('No code files found for extraction.')

    extraction = extract(code_files)
    Path('.graphify_extract.json').write_text(json.dumps(extraction, indent=2), encoding='utf-8')

    G = build_from_json(extraction)
    communities = cluster(G)
    cohesion = score_all(G, communities)
    gods = god_nodes(G)
    surprises = surprising_connections(G, communities)

    labels = {community_id: label_community(G, nodes) for community_id, nodes in communities.items()}
    questions = suggest_questions(G, communities, labels)

    report = generate(
        G,
        communities,
        cohesion,
        labels,
        gods,
        surprises,
        detection,
        {'input': extraction.get('input_tokens', 0), 'output': extraction.get('output_tokens', 0)},
        str(ROOT),
        suggested_questions=questions,
    )

    Path('graphify-out/GRAPH_REPORT.md').write_text(report, encoding='utf-8')
    to_json(G, communities, 'graphify-out/graph.json')
    if G.number_of_nodes() <= 5000:
        to_html(G, communities, 'graphify-out/graph.html', community_labels=labels or None)

    Path('.graphify_labels.json').write_text(json.dumps({str(k): v for k, v in labels.items()}, indent=2), encoding='utf-8')
    Path('.graphify_analysis.json').write_text(
        json.dumps(
            {
                'communities': {str(k): v for k, v in communities.items()},
                'cohesion': {str(k): v for k, v in cohesion.items()},
                'gods': gods,
                'surprises': surprises,
                'questions': questions,
            },
            indent=2,
        ),
        encoding='utf-8',
    )

    cost_path = OUTPUT_DIR / 'cost.json'
    if cost_path.exists():
        cost = json.loads(cost_path.read_text(encoding='utf-8'))
    else:
        cost = {'runs': [], 'total_input_tokens': 0, 'total_output_tokens': 0}
    input_tokens = extraction.get('input_tokens', 0)
    output_tokens = extraction.get('output_tokens', 0)
    cost['runs'].append(
        {
            'date': datetime.now(timezone.utc).isoformat(),
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'files': detection['total_files'],
        }
    )
    cost['total_input_tokens'] += input_tokens
    cost['total_output_tokens'] += output_tokens
    cost_path.write_text(json.dumps(cost, indent=2), encoding='utf-8')

    Path('.graphify_detect.json').write_text(json.dumps(detection, indent=2), encoding='utf-8')
    save_manifest(manifest_files)

    print(f"Curated corpus: {detection['total_files']} files · ~{detection['total_words']:,} words")
    print(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges, {len(communities)} communities")
    print('Report: graphify-out/GRAPH_REPORT.md')
    print('Graph: graphify-out/graph.json')
    if G.number_of_nodes() <= 5000:
        print('HTML: graphify-out/graph.html')


if __name__ == '__main__':
    main()