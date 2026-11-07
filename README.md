# Budget

A private spending tracker. Import your bank's CSV export, sort transactions into categories (Groceries, Coffee, Restaurants…), and see how much you spent per day, week, month or overall, with a breakdown by category and what share of your income went to needs, wants and savings.

Everything is stored in one encrypted file, `vault.json`. A tiny local server (`server.py`) serves the page and stores that file, but it only ever receives encrypted data. The page loads no external scripts and contacts nothing else.

**Use it:**
```
python3 ~/budget/server.py
```
Then open **http://localhost:8000** and log in. The first visit asks you to choose a password. Changes save automatically.

**Try it with fake data:** http://localhost:8000/?demo (nothing is saved).

---

## How it works

### Encryption
- Your password is never stored. It's typed in each time the page is unlocked.
- The password is turned into a 256-bit key with **PBKDF2-SHA256** (600,000 iterations and a random 16-byte salt). The high iteration count makes guessing passwords slow.
- The vault is encrypted with **AES-256-GCM** using the browser's built-in Web Crypto API. Every save uses a fresh random 12-byte IV.
- The vault file is JSON holding only the salt, IV, iteration count and ciphertext, none of which are secret. Without the password it's unreadable.
- GCM includes an authentication tag, so a wrong password or a tampered file makes decryption fail outright. That's how the page knows the password is wrong without storing it.
- Decrypted data and the key exist only in the tab's memory. **Lock** reloads the page, which wipes both.
- **If you lose the password, the data is gone.** There is no recovery.

### Saving
- The key from your password stays in the tab's memory after login, so saves don't need the password again. Every change (imports, categories, rules, deletions) is encrypted with a fresh IV about a second later and sent to the server with `PUT /vault.json`. The corner shows "Saved ✓".
- **The server** (`server.py`, Python standard library only):
  - Serves only `index.html` and `vault.json`. Nothing else in the folder is reachable.
  - Rejects anything that isn't shaped like an encrypted vault, so plaintext can't be saved by mistake.
  - Writes atomically (temp file, then rename), so a crash can't leave a half-written vault.
  - Keeps encrypted backups in `backups/`: at most one every 10 minutes, the last 30 kept.
  - Each save must name the version it was based on (ETag / If-Match). If the vault changed in another tab, the save is refused (409) and the page says to reload, instead of overwriting.
- If the server stops, the page shows "Not saved…" and retries every 5 seconds.
- Listens on `127.0.0.1` (this machine only) by default.

### CSV import
- The CSV is parsed in the browser (quoted fields, commas inside quotes, and `,` `;` or tab delimiters).
- You map columns once per bank: date, description, and either one amount column or separate debit/credit columns. The mapping is remembered by the file's header row.
- Amounts are normalized so that **negative = money out**. `$1,250.00` and `(10.00)` style values are handled.
- **Duplicates:** each transaction gets an ID from date + description + amount + how many times that combination appears in the file. Re-importing overlapping exports skips anything already in the vault, while two identical $5 coffees on the same day stay separate.

- **Undo an import:** each import is recorded (file name and time), and its transactions are tagged with its ID. **Imports → Delete** removes all of that import's transactions. Categories and rules are kept. Transactions imported before tracking existed appear as one "Earlier imports (untagged)" group.

### Categorizing
- No AI or external service is used. Categorizing is plain text matching.
- A **rule** is "description contains `X` → category". Matching ignores case, rules are checked top to bottom, and the first match wins.
- New transactions are categorized by the rules on import. Anything unmatched is **Uncategorized**.
- Choosing a category by hand marks that transaction as manual, and rules never overwrite it. The page then offers to turn your choice into a rule. It suggests the first words of the description, which you can edit.
- Adding, deleting or renaming rules re-runs them over all non-manual transactions.
- Each category has a type, which you can change in **Categories & rules**:
  - **Need** (Housing, Groceries, Bills & Utilities, Transport, Health) and **Want** (Restaurants, Coffee, Shopping, Entertainment) count toward Spent. A refund reduces its category's total.
  - **Savings** is money set aside, such as transfers to a savings account. It isn't counted as spending.
  - **Income** counts toward Income and is the base for the percentages.
  - **Ignore** counts toward nothing. Use it for transfers and credit-card payments so money isn't counted twice.
  - Uncategorized transactions count as spending until you categorize them.

### Auto-categorize
The **Auto-categorize (N)** button reviews uncategorized transactions one merchant at a time, starting with the biggest totals. Each card shows a guess, a confidence level and the reasons. **Enter** approves, **1/2** picks a runner-up, the dropdown picks anything else, and **S** skips. "Remember" (on by default, editable) also adds a rule for future imports.

Guesses come from scoring inside the page. No AI is used and nothing is sent anywhere:
- **Merchant names are cleaned up first:** store numbers, dates, "CITY ST" endings, bank words (`POS`, `DEBIT`, `PURCHASE AUTHORIZED ON…`) and processor prefixes (`SQ *`, `TST*`…) are removed. So `TST* JOES PIZZA #12 SEATTLE WA` becomes `JOES PIZZA`, and charges from the same merchant are reviewed together.

| Signal | Points |
|---|---|
| You filed the same merchant before (split by how often you chose each category) | up to 10 |
| Words you usually file under a category (naive Bayes over your categorized transactions) | up to 5 |
| Built-in list of common US merchants (longest match wins, so "UBER EATS" beats "UBER") | 6 |
| Generic keyword, whole word only (`PIZZA`, `PARKING`, `PHARMACY`…) | 3 |
| Paid through Toast (`TST*`) or DoorDash (`DD*`) → Restaurants | 2 |
| Same amount in 3+ different months → Bills & Utilities | 2 |
| Every charge is money coming in → Income | 1.5 |

- The category with the most points is the guess. Confidence is **High** at 8+ points (and at least double the runner-up), **Medium** at 4+, and **Low** below that.
- Suggestions for categories you've deleted or renamed are skipped.
- After every decision the model is rebuilt, so later guesses learn from earlier answers.

### Periods and charts
- **Day / Week / Month / All**, with arrows to step through periods. Weeks start on Sunday (`WEEK_START` in the code).
- **Share of income:** for the selected period, Needs, Wants, Savings and Uncategorized are each shown as a percent of that period's Income (bucket total ÷ Income). Below the bars is the share of income not yet spent or saved, or how much you went over. If a bucket exceeds 100%, the bars rescale and a marker shows where 100% is. Periods with no income (often Day or Week) show amounts only.
- Clicking a category in Spending by category filters the transaction list to it.

### Vault contents (after decryption)
```json
{
  "categories": [{ "name": "Coffee", "kind": "want" }],   // need | want | savings | income | ignore
  "rules":      [{ "text": "starbucks", "cat": "Coffee" }],
  "txns":       [{ "id": "...", "date": "2026-11-03", "desc": "STARBUCKS #12", "amount": -5.75, "cat": "Coffee", "manual": false, "imp": "<import id>" }],
  "imports":    [{ "id": "<import id>", "file": "chase-nov.csv", "at": "2026-11-07T18:00:00.000Z" }],
  "mappings":   { "<csv header signature>": { "date": 0, "desc": 1, "amt": 2, "...": "..." } }
}
```

## Hosting on another machine (e.g. an Orange Pi)

Run `python3 server.py --host 0.0.0.0` there. Two things to know:

1. **The page needs HTTPS.** Browsers only allow the Web Crypto API on `https://` or `localhost`. Over plain `http://192.168.x.x` the page won't be able to encrypt or decrypt. The easiest fix is **Tailscale** (`tailscale serve 8000` gives a private HTTPS address); Caddy with a certificate also works.
2. **Keep it private.** Nobody can read the vault without the password, but anyone who can reach the server could overwrite it. The backups limit the damage. A private network like Tailscale, or a firewall, avoids the problem.
