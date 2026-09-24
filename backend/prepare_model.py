"""One-time model download. Invoked by the user, never by capture/ingest."""
from .understanding import MODEL_CACHE, MODEL_NAME


def main():
    from sentence_transformers import SentenceTransformer
    MODEL_CACHE.mkdir(parents=True, exist_ok=True)
    print(f"Downloading/caching {MODEL_NAME}. First download may take several minutes.", flush=True)
    SentenceTransformer(MODEL_NAME, device="cpu", cache_folder=str(MODEL_CACHE), trust_remote_code=False)
    print("Model ready. You can now start the backend.", flush=True)


if __name__ == "__main__":
    main()
