from . import config, ollama


def embed(texts: list[str], timeout: float = 60, post=ollama._http_post) -> list[list[float]]:
    if not texts:
        return []
    body = {"model": config.EMBED_MODEL, "input": texts, "keep_alive": "10m"}
    return post("/api/embed", body, timeout)["embeddings"]
