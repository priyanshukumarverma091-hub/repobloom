import os
from collections import Counter

from sqlalchemy import select

from database.models import Record
from memory.project_memory import ProjectMemory

LANG = {".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript/React", ".jsx": "JavaScript/React",
        ".go": "Go", ".rs": "Rust", ".java": "Java", ".rb": "Ruby", ".php": "PHP", ".cs": "C#", ".cpp": "C++", ".c": "C",
        ".kt": "Kotlin", ".swift": "Swift", ".html": "HTML", ".css": "CSS", ".sql": "SQL", ".sh": "Shell"}
MARKERS = {"requirements.txt": "Python (pip)", "pyproject.toml": "Python (pyproject)", "package.json": "Node.js",
           "Dockerfile": "Docker", "docker-compose.yml": "Docker Compose", "go.mod": "Go modules", "Cargo.toml": "Rust/Cargo",
           "pom.xml": "Maven", "build.gradle": "Gradle", "render.yaml": "Render deployment", "Procfile": "Procfile deployment",
           ".github/workflows": "GitHub Actions CI", "alembic.ini": "Alembic migrations", "manage.py": "Django",
           "app.py": "app entry (app.py)", "main.py": "entry (main.py)", "index.js": "entry (index.js)"}


def analyze_tree(paths: list[str]) -> dict:
    ext = Counter(LANG[os.path.splitext(p)[1]] for p in paths if os.path.splitext(p)[1] in LANG)
    top = Counter(p.split("/")[0] for p in paths if "/" in p)
    names = {os.path.basename(p) for p in paths}
    markers = sorted({v for k, v in MARKERS.items() if k in names or any(p.startswith(k) for p in paths)})
    tests = sum(1 for p in paths if "test" in p.lower())
    return {"languages": ext.most_common(6), "top_dirs": top.most_common(12), "markers": markers,
            "files": len(paths), "test_files": tests}


class ArchitectureMemory(ProjectMemory):
    def refresh(self) -> int:
        tree = self.s.scalars(select(Record).where(Record.project_id == self.pid, Record.kind == "file_tree")
                              .order_by(Record.updated_at.desc())).first()
        if not tree:
            return 0
        a = analyze_tree(tree.meta.get("paths", []))
        n = 0
        if a["languages"]:
            self.upsert("architecture", "stack", "Technology stack",
                        "Languages by file count: " + ", ".join(f"{l} ({c})" for l, c in a["languages"]) +
                        (". Tooling/frameworks detected from file names: " + ", ".join(a["markers"]) if a["markers"] else "."),
                        [tree.id], note="derived from repository file tree"); n += 1
        if a["top_dirs"]:
            self.upsert("architecture", "layout", "Project layout",
                        f"{a['files']} files. Top-level directories: " + ", ".join(f"{d}/ ({c} files)" for d, c in a["top_dirs"]) +
                        f". Test-related files: {a['test_files']}.", [tree.id], note="derived from repository file tree"); n += 1
        return n
