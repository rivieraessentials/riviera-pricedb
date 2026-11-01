"""Riviera supplier price DB — backend (Phase 1 skeleton).

Layered so the money/storage core is pure stdlib (sqlite3 + Decimal) and fully
testable without the web layer; FastAPI (app.main) is a thin shell on top.
"""
