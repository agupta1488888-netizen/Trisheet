"""The live filing feed.

A background poller that keeps a table of recent SEC filings warm, so the
landing page can show what companies have filed today without any visitor
costing an EDGAR request.

This package is not part of the report pipeline. Nothing here produces a
`Fact`, and no module under `app/modules/` imports it — a feed item is read
once by a browser and rendered, never reasoned over. The dependency runs one
way: the feed borrows the pipeline's EDGAR client and its 8-K parsing, and the
pipeline knows nothing about the feed.

Layout
    universe.py  the set of companies tracked
    sources.py   EDGAR responses -> FeedItem (pure, no I/O)
    enrich.py    EX-99.1 press release -> quoted sentences
    store.py     persistence
    poller.py    the loop that drives the four above
"""
