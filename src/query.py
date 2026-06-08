import argparse
import json
import sys

# Import central RAG functions
from core_rag import generate_response, query_db


def parse_arguments():
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Query the local Monorepo RAG system using ChromaDB and Gemini."
    )
    parser.add_argument(
        "--query",
        type=str,
        required=True,
        help="The question or search query you want to ask about the codebase.",
    )
    parser.add_argument(
        "--scope",
        type=str,
        choices=["frontend", "backend"],
        default=None,
        help="Filter results by scope: 'frontend' (Angular) or 'backend' (NestJS/Fastify/Prisma).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results in JSON format instead of human-readable text.",
    )
    return parser.parse_args()


def main():
    args = parse_arguments()
    is_json_mode = args.json

    if not is_json_mode:
        print(f"Analyzing query: '{args.query}'")
        if args.scope:
            print(f"Applying scope filter: '{args.scope}'")
        print("Generating query embedding & querying ChromaDB...")

    try:
        results = query_db(args.query, args.scope)
    except Exception as e:
        print(f"Failed to query database: {e}", file=sys.stderr)
        sys.exit(1)

    if not is_json_mode:
        print("Generating answer using gemini-2.5-flash...")

    # We respond in English when outputting structured JSON for another AI agent
    answer = generate_response(args.query, results, respond_in_english=is_json_mode)

    if is_json_mode:
        # Output structured JSON for AI consumption
        output_data = {
            "query": args.query,
            "scope": args.scope,
            "retrieved_chunks": [],
            "response": answer,
        }
        documents = results.get("documents")
        metadatas = results.get("metadatas")
        ids = results.get("ids")

        docs_list = documents[0] if documents else []
        meta_list = metadatas[0] if metadatas else []
        ids_list = ids[0] if ids else []

        for i in range(len(docs_list)):
            meta = meta_list[i] if i < len(meta_list) else {}
            output_data["retrieved_chunks"].append(
                {
                    "id": ids_list[i] if i < len(ids_list) else f"chunk_{i}",
                    "source": meta.get("source", "unknown"),
                    "scope": meta.get("scope", "unknown"),
                    "start_line": meta.get("start_line", 1),
                    "end_line": meta.get("end_line", 1),
                    "content": docs_list[i],
                }
            )
        print(json.dumps(output_data, indent=2, ensure_ascii=False))
    else:
        # Human-readable console format
        print("\n" + "=" * 40 + " CONTEXTO RECUPERADO " + "=" * 40)
        retrieved_metadatas = results.get("metadatas")
        metadatas = retrieved_metadatas[0] if retrieved_metadatas else []
        for idx, meta in enumerate(metadatas):
            source = meta.get("source")
            lines = f"L{meta.get('start_line')}-{meta.get('end_line')}"
            print(f"[{idx + 1}] {source} ({lines})")

        print("\n" + "=" * 40 + " RESPUESTA RAG " + "=" * 40)
        print(answer)
        print("=" * 101)


if __name__ == "__main__":
    main()
