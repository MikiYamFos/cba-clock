import uvicorn
from fastapi import FastAPI, Query
from typing import Annotated

from worker.app.es_search import CBASectionSearcher

app = FastAPI()
searcher = CBASectionSearcher()


@app.get("/search")
def search_all(
    q: Annotated[str, Query(min_length=1)],
    phrase: bool = False,
    size: int = 20,
):
    result = searcher.search_cross_cba(query=q, size=size, phrase_match=phrase)
    return result


@app.get("/search/{source_file}")
def search_within(
    source_file: str,
    q: Annotated[str, Query(min_length=1)],
    phrase: bool = False,
    size: int = 20,
):
    result = searcher.search_within_cba(
        query=q, source_file=source_file, size=size, phrase_match=phrase
    )
    return result


def main():
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
