# 发布到 GitHub 指南

> 仓库已初始化完成，本地 2 个提交，可直接推送。

---

## 第一步：在 GitHub 创建空仓库

1. 打开 https://github.com/new
2. 填写：
   - **Repository name**：`invoice-assistant`（或你喜欢的名字）
   - **Description**：`中文电子发票整理工具 · 本地运行 · 自动解析/校验/分类/导出 Excel`
   - **Visibility**：Public（开源）或 Private（先私有试用）
3. **不要勾选** "Add a README file"、"Add .gitignore"、"Choose a license"
   （本地已经有了，勾选会造成冲突）
4. 点 **Create repository**

---

## 第二步：关联并推送

创建完成后，GitHub 会显示仓库地址。在 `invoice-assistant` 目录里执行：

```bash
cd "E:/大学/工坊/2026轮式/invoice-assistant"

# 换成你自己的用户名
git remote add origin https://github.com/<你的用户名>/invoice-assistant.git

# 默认分支改为 main（GitHub 现在的约定）
git branch -M main

git push -u origin main
```

### 如果要求登录

推荐用 **Personal Access Token**（比密码可靠）：

1. GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Generate new token，勾选 `repo` 权限
3. 推送时用户名填 GitHub 用户名，密码位置粘贴这个 token

或用 GitHub CLI（更省事）：

```bash
winget install GitHub.cli
gh auth login
gh repo create invoice-assistant --public --source=. --push
```

---

## 第三步：补充仓库信息（建议）

推送后在网页上做：

| 项目 | 建议 |
| :--- | :--- |
| **Topics** | `invoice` `chinese` `tax` `excel` `pdf` `python` `mcp` |
| **About** | 勾选 "Releases"、"Packages" 如果后续要发版 |
| **License** | 已在仓库里（MIT），GitHub 会自动识别 |

---

## 推送前请再确认一遍

```bash
cd <仓库目录>

# 1. 确认没有发票原件被跟踪（应无输出）
git ls-files | grep -Ei "\.pdf$|\.ofd$|\.zip$|\.jpg$|\.png$"

# 2. 确认没有令牌泄露（应无输出；令牌是 20 位十六进制随机串）
git grep -nE "\b[0-9a-f]{20}\b" $(git rev-list --all) -- . | head

# 3. 确认没有配置文件（应无输出）
git ls-files | grep -E "config\.json|categories\.json|scan-data\.js|\.发票助手"
```

三条都应**没有输出**。若出现内容，立刻：

```bash
git rm --cached <文件>
# 若令牌已进入历史提交，需要改写历史后重新提交
```

---

## 后续维护

```bash
# 改完代码
git add -A
git commit -m "fix: 描述改了什么"
git push

# 打版本标签
git tag -a v1.0.0 -m "首个可用版本"
git push origin v1.0.0
```

---

## 迁移到另一台电脑

仓库本身就是完整的程序包：

```bash
git clone https://github.com/<用户名>/invoice-assistant.git
cd invoice-assistant
pip install -r requirements.txt
python serve_api.py
```

**推荐布局**（程序自动识别发票目录，无需配置）：

```
我的发票/
├── 发票助手/          ← clone 到这里
│   ├── serve_api.py
│   └── web/
├── 一堆发票.pdf
└── 子文件夹/          ← 也会被递归扫描
```

若程序不在发票目录内，用环境变量指定：

```bash
set INVOICE_ROOT=D:\我的发票
```
