"""CLI do ContextClaude: cc.py buscar "<consulta>" [-p projeto] [-n 5] | cc.py indexar | cc.py retomar <id>
| cc.py notas [-p projeto] [--todas] | cc.py contexto [projeto] [--mostrar]"""
import argparse
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from contextclaude import db, graph, lock, logutil, notes, ollama, pipeline, vectors  # noqa: E402


def main(argv=None, out=print) -> int:
    ap = argparse.ArgumentParser(prog="cc.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("buscar", help="busca semântica na memória")
    b.add_argument("consulta")
    b.add_argument("-p", "--projeto")
    b.add_argument("-n", type=int, default=5)
    sub.add_parser("app", help="abre a janela do ContextClaude")
    sub.add_parser("atalho", help="cria o atalho do app no Menu Iniciar e na Área de Trabalho")
    sub.add_parser("indexar", help="reconstrói o índice a partir do SQLite")
    g = sub.add_parser("grafo", help="mostra os vizinhos de uma entidade no grafo de conhecimento")
    g.add_argument("entidade")
    g.add_argument("-p", "--projeto")
    g.add_argument("-d", "--depth", type=int, default=2)
    v = sub.add_parser("vault", help="reconstrói as notas do Obsidian a partir do SQLite, para um projeto")
    v.add_argument("-p", "--projeto", required=True)
    r = sub.add_parser("retomar", help="devolve à fila uma sessão em erro (id ou prefixo do id)")
    r.add_argument("sessao")
    nt = sub.add_parser("notas", help="refaz título/resumo/feitos das sessões antigas (1 chamada ao LLM por sessão)")
    nt.add_argument("-p", "--projeto")
    nt.add_argument("--todas", action="store_true", help="refaz também as sessões que já têm título")
    c = sub.add_parser("contexto", help="gera o _contexto.md (briefing para o Claude) de um projeto ou de todos")
    c.add_argument("projeto", nargs="?")
    c.add_argument("--mostrar", action="store_true", help="imprime o conteúdo em vez do caminho")
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
        elif args.cmd == "retomar":
            conn = db.connect()
            try:
                ids = [x[0] for x in conn.execute(
                    "SELECT session_id FROM sessions WHERE session_id LIKE ? || '%'", (args.sessao,))]
                if len(ids) != 1:
                    out("Nenhuma sessão com esse id." if not ids else f"Prefixo ambíguo: {len(ids)} sessões.")
                    return 1
                db.reset_session(conn, ids[0])
                out(f"Sessão {ids[0]} devolvida à fila.")
            finally:
                conn.close()
        elif args.cmd == "contexto":
            conn = db.connect()
            try:
                alvos = [args.projeto] if args.projeto else notes.projects(conn)
                if args.projeto and args.projeto not in notes.projects(conn):
                    out(f"Projeto desconhecido: {args.projeto}. Conhecidos: {', '.join(notes.projects(conn))}")
                    return 1
                for p in alvos:
                    if args.mostrar:
                        out(notes.build_context(conn, p))
                    else:
                        out(str(notes.write_context(conn, p)))
            finally:
                conn.close()
        elif args.cmd == "notas":
            handle = lock.acquire()
            if handle is None:
                out("O worker está rodando; tente de novo quando a fila terminar.")
                return 1
            try:
                if not ollama.ensure_running():
                    out("Ollama não respondeu; nada feito.")
                    return 1
                conn = db.connect()
                try:
                    n = pipeline.backfill_records(conn, project=args.projeto, todas=args.todas, out=out)
                    out(f"{n} sessões refeitas.")
                finally:
                    conn.close()
            finally:
                lock.release(handle)
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
        elif args.cmd == "app":
            from app import main as app_main
            return app_main.main()
        elif args.cmd == "atalho":
            from app import shortcut
            dirs = (shortcut.start_menu_dir(), shortcut.desktop_dir())
            for d in dirs:
                out(str(shortcut.create_shortcut(d)))
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
        elif args.cmd == "retomar":
            out(f"Falha ao retomar sessão: {e!r}")
        elif args.cmd == "vault":
            out(f"Falha ao reconstruir vault: {e!r}")
        elif args.cmd in ("notas", "contexto", "app", "atalho"):
            out(f"Falha em {args.cmd}: {e!r}")
            if args.cmd in ("app", "atalho"):  # sob pythonw não há console: o log é o único registro
                logutil.log(f"Falha em {args.cmd}: {e!r}\n{traceback.format_exc()}")
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
    if sys.stdout:  # None sob pythonw
        sys.stdout.flush()
    os._exit(code)
