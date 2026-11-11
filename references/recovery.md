# Recovery and uncertainty

The implementation is synchronous and resumable, not an autonomous daemon. Preserve one private run directory, use its existing manifest/publication/application files, and run one writer at a time. Credentials remain in the environment; state contains only necessary IDs, hashes, paths and operation receipts. Keep it private even though it contains no tokens.

## What can resume automatically

Verified uploads and matching existing packs are reused. A completed topic assignment is read back and not rewritten. Accepted pack additions are recognized by exact pack content/order and `file_unique_id`. After accepted icon edits, bounded read-only retries reconcile stale exact responses; another bounded full-inventory check handles stale pagination. Publication and application are separate commands and receipts.

## What deliberately stops

An unexpected prior icon, a changed title/pin/closed/hidden state, missing General, changed inventory count/identity, mismatched manifest/bot/owner, an unrelated existing pack name, an altered prepared file, or duplicate designs/pixels/emoji IDs stops the run.

A prior pack creation that remains absent and a prior addition that cannot be proved present are unresolved. A pending topic edit that cannot be read back as its intended ID is also unresolved. The helper never blindly resends these operations. A crash between recording intent and actually sending the write can therefore require operator reconciliation. This conservative stop is intentional; it is not a claim of exactly-once delivery.

Read the exact target and owning service's operation evidence through the already-authorized runtime. If that proves no write occurred, a new bounded retry needs explicit operator authority and an honestly reconciled new run/state transition. Do not silently delete pending markers, rewrite receipts, recreate packs or use another account to make the CLI green. If the runtime is unavailable, report that concrete blocker and retain the last verified state.

## Rollback

Each application receipt includes the old icon and an expected-current-icon rollback payload. Rollback is not automatic and is not authorized by a read-only verification request. With exact approval, re-read the target, require the current icon still equals the value installed by this run, apply only the old icon, then verify the result and preserved non-icon fields. Never overwrite somebody else's later change.

## Storage hygiene

Keep only needed source assets, provenance and final run receipts. Dispose of your own confirmed-unneeded demo images, fixture directories and verification clones after checking their exact paths and active consumers. Do not back up whole runtimes or delete unrelated files. Run outputs never belong in the reusable public repository. The code uses atomic JSON replacement and local advisory locks, but does not claim remote filesystem locking, power-loss durability of directory entries or cross-host transactionality.
