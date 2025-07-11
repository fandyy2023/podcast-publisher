# Repository Workflow Cheat-Sheet

This project (and its sibling *madeira_rentals_search*) live in your **fandyy2023** GitHub account.

## 1. Repository Mapping

| Project | Local Path | Remote URL | Default Branch |
|---------|------------|------------|----------------|
| podcast-publisher | `/Users/poolpooly/CascadeProjects/podcast-publisher` | `https://github.com/fandyy2023/podcast-publisher.git` | `main` |
| madeira_rentals_search | `/Users/poolpooly/CascadeProjects/madeira_rentals_search` | `https://github.com/fandyy2023/rentals_search.git` | `main` |

Notes for **podcast-publisher**:
* Large media (audio, images) are **ignored** via `.gitignore`. They stay only on disk or should be stored via Git LFS/cloud.

## 2. Typical Push Workflow

```bash
# 1. Check status
git status

# 2. Stage changes (all or selective)
git add -A            # or: git add <files>

# 3. Commit
git commit -m "Meaningful message"

# 4. Push to default branch
git push origin main
```

### Feature Branches
```bash
# Create and switch
git checkout -b feature_name
# ... commit as usual ...

git push -u origin feature_name   # first push
```

After the first push you can open a Pull Request on GitHub if review is needed.

## 3. Updating Local Code
```bash
git pull origin main   # fetch latest changes before starting work
```

## 4. Troubleshooting
* If `git push` is rejected due to large files (>100 MB), either add them to `.gitignore` or use Git LFS.
* Ensure you’re inside the correct project directory before running git commands.

---
_Last update: 2025-07-11_
