import json
from pathlib import Path
from graphify.detect import detect

# Detect with exclusions for large data dirs
result = detect(
    Path('.'),
    exclude_patterns=[
        'songs-pipeline/downloads/*',
        '*/Library/*',
        '*/Temp/*', 
        '*/Logs/*',
        '*.db',
        '*.sqlite',
        '.git/*',
    ]
)

Path('.graphify_detect.json').write_text(json.dumps(result))
print(f"Corpus: {result.get('total_files', 0)} files · ~{result.get('total_words', 0):,} words")
files = result.get('files', {})
for name in ('code', 'docs', 'papers', 'images', 'video'):
    count = len(files.get(name, []))
    if count:
        print(f"  {name}:     {count} files")
