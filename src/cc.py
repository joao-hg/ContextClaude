"""CLI do ContextClaude: cc.py buscar "<consulta>" [-p projeto] [-n 5] | cc.py indexar"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from contextclaude import db, graph, lock, notes, ollama, vectors  # noqa: E402


def main(argv=None, out=print) -> int:
    ap = argparse.ArgumentParser(prog="cc.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("buscar", help="busca semântica na memória")
    b.add_argument("consulta")
    b.add_argument("-p", "--projeto")
    b.add_argument("-n", type=int, default=5)
    sub.add_parser("indexar", help="reconstrói o índice a partir do SQLite")
    g = sub.add_parser("grafo", help="mostra os vizinhos de uma entidade no grafo de conhecimento")
    g.add_argument("entidade")
    g.add_argument("-p", "--projeto")
    g.add_argument("-d", "--depth", type=int, default=2)
    v = sub.add_parser("vault", help="reconstrói as notas do Obsidian a partir do SQLite, para um projeto")
    v.add_argument("-p", "--projeto", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "buscar" and args.n < 1:
        out("-n deve ser pelo menos 1.")
        return 1
    if args.cmd == "grafo" and args.depth < 1:
        out("-d deve ser pelo menos 1.")
        return 1
    try:
        if args.cmd == "buscar":
            ollama.ensure_running()
            hits = vectors.search(args.consulta, projeto=args.projeto, n=args.n)
            conn = db.connect()
            try:
                active = db.active_fact_ids(conn, [h["id"] for h in hits])
            finally:
                conn.close()
            hits = [h for h in hits if h["id"] in active]
            if not hits:
                out("Nada encontrado.")
            for h in hits:
                out(f"[{h['projeto']}/{h['tipo']} {h['data'][:10]}] {h['text']} (dist {h['distance']:.2f})")
        elif args.cmd == "grafo":
            conn = db.connect()
            try:
                hits = graph.neighbors(conn, args.entidade, projeto=args.projeto, depth=args.depth)
            finally:
                conn.close()
            if not hits:
                out("Nada encontrado.")
            for h in hits:
                out(f"{h['origem']} --{h['relacao']}--> {h['destino']}")
        elif args.cmd == "vault":
            handle = lock.acquire()
            if handle is None:
                out("O worker está rodando; tente de novo em instantes.")
                return 1
            try:
                conn = db.connect()
                try:
                    out(f"{notes.rebuild_project(conn, args.projeto)} notas reconstruídas.")
                finally:
                    conn.close()
            finally:
                lock.release(handle)
        else:
            handle = lock.acquire()  # o mesmo lock do worker: evita reconstruir enquanto ele indexa
            if handle is None:
                out("O worker está rodando; tente de novo em instantes.")
                return 1
            try:
                ollama.ensure_running()
                conn = db.connect()
                try:
                    out(f"{vectors.rebuild(conn)} itens indexados.")
                finally:
                    conn.close()
            finally:
                lock.release(handle)
    except Exception as e:
        if args.cmd == "buscar":
            out(f"Falha na busca: {e!r}")
        elif args.cmd == "grafo":
            out(f"Falha no grafo: {e!r}")
        elif args.cmd == "vault":
            out(f"Falha ao reconstruir vault: {e!r}")
        else:
            out(f"Falha ao indexar: {e!r}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    code = main()
    sys.stdout.flush()
    os._exit(code)
