import tree_sitter_language_pack as tslp
import os
import shutil

def verify():
    # 1. Check current downloaded languages
    print("Initial languages:", tslp.downloaded_languages())
    
    target = "go"
    if target in tslp.downloaded_languages():
        print(f"Removing {target} from cache for clean test...")
        # We don't have a direct 'remove' but we can clean all or just ignore
        # tslp.clean_cache() # This might be too destructive if we want to keep others
    
    # 2. Try processing 'go' code - this previously failed if not cached
    source = "package main\nfunc main() {}"
    print(f"Processing {target} code (should trigger auto-download)...")
    
    try:
        # Use a config that only specifies language
        result = tslp.process(source, tslp.ProcessConfig(target))
        print("Success! Processed language:", result['language'])
        print("Metrics:", result['metrics'])
    except Exception as e:
        print("Failed as expected (before fix) or error occurred:", e)
        return False

    # 3. Verify it's now in the cache
    final_langs = tslp.downloaded_languages()
    print("Final languages:", final_langs)
    if target in final_langs:
        print("Verification PASSED: Language auto-downloaded on process()")
        return True
    else:
        print("Verification FAILED: Language not in cache after process()")
        return False

if __name__ == "__main__":
    verify()
