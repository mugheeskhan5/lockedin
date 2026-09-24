"""Print 20 random rows for manual eyeballing; no assertions or test harness."""
import sys
from contextlib import closing
from .db import connect


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    with closing(connect()) as connection:
        for row in connection.execute("""
            SELECT r.shortcode,r.caption,r.ocr_text,length(r.embedding) AS embedding_bytes,
                   j.status,j.frame_status,j.ocr_status,j.last_error
            FROM reels r LEFT JOIN understanding_jobs j ON j.reel_id=r.id
            ORDER BY RANDOM() LIMIT 20
        """):
            print(f"\n{row['shortcode']} | job={row['status']} | frame={row['frame_status']} | OCR={row['ocr_status']} | embedding_bytes={row['embedding_bytes']}")
            print("Caption:", row["caption"] or "(empty)")
            print("OCR:    ", row["ocr_text"] or "(empty / skipped)")
            if row["last_error"]:
                print("Reason: ", row["last_error"])
            print(f"Link: https://www.instagram.com/reel/{row['shortcode']}/")


if __name__ == "__main__":
    main()
