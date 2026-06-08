from pathlib import Path
from typing import Any, cast

# Import central RAG functions
from core_rag import REPO_ROOT, get_chroma_collection, get_embeddings

# Configuration parameters
MAX_LINES_PER_CHUNK = 50
OVERLAP_LINES = 10
BATCH_SIZE = 30

# Paths to scan and directories to ignore
SCAN_DIRS = ["frontend", "backend"]
IGNORE_DIRS = {
    "node_modules",
    "dist",
    ".git",
    ".angular",
    ".next",
    "build",
    ".chromadb",
    "rag-local",
    ".agents",
    "scripts-test",
    "specs",
    "generated",
}
ALLOWED_EXTENSIONS = {".ts", ".html", ".prisma"}


def get_relative_path(path: Path) -> str:
    """Returns the path relative to the repository root, using forward slashes."""
    try:
        rel_path = path.relative_to(REPO_ROOT)
        return str(rel_path).replace("\\", "/")
    except ValueError:
        return str(path)


def scan_files() -> list[Path]:
    """Recursively scans the monorepo for .ts, .html and .prisma files in targeted folders."""
    files_to_process = []
    for dir_name in SCAN_DIRS:
        target_dir = REPO_ROOT / dir_name
        if not target_dir.exists() or not target_dir.is_dir():
            print(f"Warning: Scan directory '{target_dir}' does not exist.")
            continue

        for path in target_dir.rglob("*"):
            if path.is_file() and path.suffix in ALLOWED_EXTENSIONS:
                parts = path.relative_to(REPO_ROOT).parts
                if not any(ignored in parts for ignored in IGNORE_DIRS):
                    files_to_process.append(path)
    return files_to_process


def chunk_file(file_path: Path) -> list[dict]:
    """Splits a file into overlapping chunks of lines.

    Ensures lines are kept intact.
    """
    chunks = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Error reading file {file_path}: {e}")
        return []

    total_lines = len(lines)
    if total_lines == 0:
        return []

    if total_lines <= MAX_LINES_PER_CHUNK:
        text = "".join(lines)
        chunks.append({"text": text, "start_line": 1, "end_line": total_lines})
        return chunks

    start = 0
    while start < total_lines:
        end = min(start + MAX_LINES_PER_CHUNK, total_lines)
        chunk_lines = lines[start:end]
        text = "".join(chunk_lines)

        chunks.append({"text": text, "start_line": start + 1, "end_line": end})

        start += MAX_LINES_PER_CHUNK - OVERLAP_LINES
        if start >= total_lines - OVERLAP_LINES:
            break

    return chunks


def run_ingestion() -> dict:
    """Executes the full scan and chunking process, indexing everything into ChromaDB.

    Returns a dictionary with ingestion statistics.
    """
    print("Starting monorepo ingestion process (Modular /src layout)...")
    print(f"Monorepo Root: {REPO_ROOT.resolve()}")

    # 1. Scan files
    files = scan_files()
    print(f"Found {len(files)} source files to analyze.")

    # 2. Extract chunks from all files
    all_chunks = []
    for file_path in files:
        rel_path = get_relative_path(file_path)
        scope = "frontend" if "frontend" in rel_path.split("/") else "backend"

        file_chunks = chunk_file(file_path)
        for chunk in file_chunks:
            chunk["source"] = rel_path
            chunk["scope"] = scope
            all_chunks.append(chunk)

    total_chunks = len(all_chunks)
    print(f"Split files into {total_chunks} chunks.")

    stats = {
        "status": "success",
        "total_files": len(files),
        "total_chunks": total_chunks,
        "indexed_chunks": 0,
        "collection_count": 0,
        "error": None
    }

    if total_chunks == 0:
        print("No content to index. Exiting.")
        return stats

    # 3. Get persistent ChromaDB collection
    try:
        collection = get_chroma_collection()
    except Exception as e:
        print(f"Failed to connect to ChromaDB: {e}")
        stats["status"] = "error"
        stats["error"] = str(e)
        return stats

    # 4. Generate embeddings and index in batches
    print(f"Indexing {total_chunks} chunks in batches of {BATCH_SIZE}...")
    success_count = 0

    for i in range(0, total_chunks, BATCH_SIZE):
        batch = all_chunks[i : i + BATCH_SIZE]
        batch_texts = [c["text"] for c in batch]

        print(
            f"Processing batch {i // BATCH_SIZE + 1}/{(total_chunks - 1) // BATCH_SIZE + 1} "
            f"(chunks {i} to {min(i + BATCH_SIZE, total_chunks)})..."
        )

        try:
            embeddings = get_embeddings(batch_texts)
            if not embeddings:
                print("Skipping batch due to API issues.")
                continue

            ids = []
            documents = []
            metadatas = []

            for idx, chunk in enumerate(batch):
                chunk_id = f"{chunk['source']}#L{chunk['start_line']}-{chunk['end_line']}"
                ids.append(chunk_id)
                documents.append(chunk["text"])
                metadatas.append(
                    {
                        "source": chunk["source"],
                        "scope": chunk["scope"],
                        "start_line": chunk["start_line"],
                        "end_line": chunk["end_line"],
                    }
                )

            collection.upsert(
                ids=ids,
                embeddings=cast(Any, embeddings),
                documents=documents,
                metadatas=cast(Any, metadatas),
            )
            success_count += len(batch)

        except Exception as e:
            print(f"Error indexing batch starting at index {i}: {e}")
            print("Continuing with next batch...")

    collection_count = collection.count()
    print("\nIngestion completed successfully!")
    print(f"Total chunks indexed: {success_count}/{total_chunks}")
    print(f"ChromaDB collection count: {collection_count}")

    stats["indexed_chunks"] = success_count
    stats["collection_count"] = collection_count
    return stats


def main():
    run_ingestion()


if __name__ == "__main__":
    main()
