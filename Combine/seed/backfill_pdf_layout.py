"""Store word boxes for PDFs uploaded before migration 0016: `python -m seed.backfill_pdf_layout` (uses the configured Supabase)."""

from app.config import get_settings
from app.services import pdf_view
from app.store.supabase_store import SupabaseStore

if __name__ == "__main__":
    print(f"Filled word boxes for {pdf_view.backfill(SupabaseStore(get_settings()))} PDF(s)")
