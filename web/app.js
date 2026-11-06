/* ============================================================
   发票助手 · 前端交互
   数据来源：本机 API /api/invoices（令牌来自 /api/bootstrap）
   ============================================================ */

(function () {
  'use strict';

  // ---------------------------------------------------------- 状态
  const state = {
    items: [],                 // 全部发票
    categories: [],            // {id,name,color,collapsed}
    activeCat: '__all__',      // '__all__' | '__uncat__' | categoryId
    selected: new Set(),       // 选中的 item id
    sortKey: 'filename',
    sortDir: 1,                // 1 升序 / -1 降序
    keyword: '',
    menuEl: null,
    apiToken: '',
  };

  const COLORS = ['deepseek', 'green', 'amber', 'red', 'violet', 'cyan', 'pink'];

  // 颜色会被拼进 class 名，只认预设值
  const safeColor = c => (COLORS.indexOf(c) >= 0 ? c : 'deepseek');

  // ---------------------------------------------------------- 工具
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  const money = n => '¥' + Number(n || 0).toLocaleString('zh-CN', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });

  const esc = s => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

  function toast(msg, ms) {
    const wrap = $('#toastWrap');
    const el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    wrap.appendChild(el);
    setTimeout(() => {
      el.style.transition = 'opacity .2s ease';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 220);
    }, ms || 2200);
  }

  // 发票唯一 id
  const itemId = it => it.rel_path;

  // ---------------------------------------------------------- 鉴权
  /** 确保已拿到 API 令牌（bootstrap 免令牌，仅回环） */
  function ensureToken() {
    if (state.apiToken) return Promise.resolve(state.apiToken);
    return fetch('/api/bootstrap')
      .then(r => r.json())
      .then(d => {
        state.apiToken = d.token || '';
        if (d.mcp) state.mcpInfo = d.mcp;
        return state.apiToken;
      })
      .catch(() => '');
  }

  // ---------------------------------------------------------- 分类读写（后端持久化）
  /** 统一的后端调用：自动带令牌、统一错误提示 */
  function apiCall(method, path, body) {
    const showErr = d => {
      toast('操作失败：' + (d && d.error ? d.error : '未知错误'), 3200);
      return null;
    };
    return ensureToken()
      .then(tok => fetch(path, {
        method,
        headers: Object.assign({ 'X-Api-Token': tok },
          body ? { 'Content-Type': 'application/json' } : {}),
        body: body ? JSON.stringify(body) : undefined,
      }))
      .then(r => r.json())
      .then(d => (d && d.error) ? showErr(d) : d)
      .catch(e => showErr({ error: e.message }));
  }

  /** 拉取分类与分配关系；发票的 category 由 /api/invoices 一并返回 */
  function loadCategories() {
    return apiCall('GET', '/api/categories').then(d => {
      if (!d || !d.categories) return;
      state.categories = d.categories;
      render();
    });
  }

  // ---------------------------------------------------------- 初始化
  /** 从后端重新拉取发票数据（分类由后端叠加，不再本地保留） */
  function loadData(options) {
    return ensureToken()
      .then(tok => fetch('/api/invoices?limit=5000', {
        headers: { 'X-Api-Token': tok },
      }))
      .then(r => r.json())
      .then(d => {
        if (d.error) throw new Error(d.error);
        state.items = d.items.map(it => Object.assign({}, it, {
          category: it.category || null,
        }));
        if (d.root) $('#rootPath').textContent = d.root;
        render();
        return d;
      });
  }

  function init() {
    // 分类不预置：全部持久化在 .发票助手/categories.json，由用户手动建立
    state.categories = [];

    bindEvents();
    render();

    // 数据只走 API（不再有 web/demo-data.js 这类免鉴权静态数据文件）
    Promise.all([loadCategories(), loadData()])
      .then(() => { /* 首次渲染完成 */ })
      .catch(() => {
        $('#rootPath').textContent = '扫描目录读取失败';
        toast('无法连接本地服务，请通过启动脚本打开本页面', 5000);
      });
  }

  // ---------------------------------------------------------- 视图数据
  function viewItems() {
    let list = state.items;

    // 分组筛选
    if (state.activeCat === '__uncat__') {
      list = list.filter(it => !it.category);
    } else if (state.activeCat !== '__all__') {
      list = list.filter(it => it.category === state.activeCat);
    }

    // 关键字
    const kw = state.keyword.trim().toLowerCase();
    if (kw) {
      list = list.filter(it =>
        (it.filename || '').toLowerCase().includes(kw) ||
        (it.seller || '').toLowerCase().includes(kw) ||
        (it.invoice_no || '').toLowerCase().includes(kw) ||
        (it.item || '').toLowerCase().includes(kw)
      );
    }

    // 排序
    const k = state.sortKey, d = state.sortDir;
    list = list.slice().sort((a, b) => {
      let va = a[k], vb = b[k];
      if (k === 'amount') {
        // 空金额永远排最后，不参与方向翻转
        const na = va == null, nb = vb == null;
        if (na && nb) return 0;
        if (na) return 1;
        if (nb) return -1;
        return (va - vb) * d;
      }
      va = String(va == null ? '' : va);
      vb = String(vb == null ? '' : vb);
      return va.localeCompare(vb, 'zh-CN') * d;
    });

    return list;
  }

  // 计账判定：重复 / 容器包 / 解析失败不计（与后端 is_counted() 保持一致）
  const isCounted = it =>
    it.status !== 'dup' && it.status !== 'container' && it.status !== 'error'
    && it.amount != null;

  // ---------------------------------------------------------- 渲染
  function render() {
    renderSummary();
    renderCategories();
    renderTable();
    renderActionBar();
  }

  function renderSummary() {
    const counted = state.items.filter(isCounted);
    const total = counted.reduce((s, it) => s + (it.amount || 0), 0);

    $('#sumTotal').textContent = money(total);
    $('#sumCount').textContent = counted.length;
    $('#sumFiles').textContent = state.items.length;
    $('#sumDup').textContent = state.items.filter(it => it.status === 'dup').length;
    $('#sumUncat').textContent = state.items.filter(it => !it.category && isCounted(it)).length;
  }

  function catStats(catId) {
    let list;
    if (catId === '__all__') {
      list = state.items;
    } else if (catId === '__uncat__') {
      list = state.items.filter(it => !it.category);
    } else {
      list = state.items.filter(it => it.category === catId);
    }
    const counted = list.filter(isCounted);
    return {
      count: counted.length,
      total: counted.reduce((s, it) => s + (it.amount || 0), 0),
      raw: list.length,
    };
  }

  // 「全部发票」只统计，不重复计入左侧合计
  function sidebarTotal() {
    return state.items.filter(isCounted).length;
  }

  function renderCategories() {
    const box = $('#catList');
    box.innerHTML = '';

    const groups = [
      { id: '__all__', name: '全部发票', color: 'deepseek' },
      { id: '__uncat__', name: '待归类', color: null },
    ].concat(state.categories.map(c => ({ id: c.id, name: c.name, color: c.color })));

    let totalCount = 0;

    groups.forEach(g => {
      const st = catStats(g.id);
      // 「全部发票」是汇总视图，不计入左侧合计，避免与各分类重复相加
      if (g.id !== '__all__') totalCount += st.count;

      const isCat = g.id !== '__all__' && g.id !== '__uncat__';
      const catObj = isCat ? state.categories.find(c => c.id === g.id) : null;
      const collapsed = catObj ? catObj.collapsed : false;
      const active = state.activeCat === g.id;

      const el = document.createElement('div');
      el.className = 'group' + (active ? ' active' : '') + (collapsed ? ' collapsed' : '');
      el.dataset.cat = g.id;

      const dotCls = g.color ? ' c-' + safeColor(g.color) : '';
      const caretSvg = isCat
        ? `<svg width="9" height="9" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
             <path d="M4 6.5 8 10.5l4-4" stroke-linecap="round" stroke-linejoin="round"/>
           </svg>`
        : '';

      const ops = isCat ? `
        <span class="group-ops">
          <button class="group-op" data-op="rename" title="重命名">
            <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M11.2 2.3 13.7 4.8 5.4 13.1 2.4 13.6l.5-3z" stroke-linejoin="round"/>
            </svg>
          </button>
          <button class="group-op" data-op="delete" title="删除分类">
            <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.2a1 1 0 0 0 1 .8h3.8a1 1 0 0 0 1-.8l.6-8.2"
                    stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </button>
        </span>` : '';

      el.innerHTML = `
        <button class="group-head" data-cat="${g.id}">
          <span class="group-caret">${caretSvg}</span>
          <span class="group-dot${dotCls}"></span>
          <span class="group-name">${esc(g.name)}</span>
          <span class="group-count">${st.count}</span>
        </button>
        ${ops}
        <div class="group-sub">${money(st.total)}</div>
      `;

      // 行内操作：重命名 / 删除
      $$('.group-op', el).forEach(btn => {
        btn.addEventListener('click', ev => {
          ev.stopPropagation();
          const op = btn.dataset.op;
          if (op === 'rename') {
            const nm = prompt('重命名分类', catObj.name);
            if (!nm || !nm.trim()) return;
            const v = nm.trim();
            if (state.categories.some(c => c.id !== catObj.id && c.name === v)) {
              toast('该名称已存在');
              return;
            }
            apiCall('PATCH', '/api/categories/' + encodeURIComponent(catObj.id),
              { name: v })
              .then(d => {
                if (!d) return;
                catObj.name = v;
                toast(`已重命名为「${v}」`);
                render();
              });
          } else if (op === 'delete') {
            const n = state.items.filter(it => it.category === catObj.id).length;
            const msg = n
              ? `删除分类「${catObj.name}」？其中 ${n} 张发票将回到「待归类」。`
              : `删除分类「${catObj.name}」？`;
            if (!confirm(msg)) return;
            apiCall('DELETE', '/api/categories/' + encodeURIComponent(catObj.id))
              .then(d => {
                if (!d) return;
                state.items.forEach(it => { if (it.category === catObj.id) it.category = null; });
                state.categories = state.categories.filter(c => c.id !== catObj.id);
                if (state.activeCat === catObj.id) state.activeCat = '__uncat__';
                toast(`已删除分类「${catObj.name}」`);
                render();
              });
          }
        });
      });

      // 点击分组：切换视图
      $('.group-head', el).addEventListener('click', e => {
        // 点箭头 = 折叠/展开（必须最先判断，且阻止冒泡到视图切换）
        const caret = e.target.closest('.group-caret');
        if (caret && catObj) {
          e.preventDefault();
          e.stopPropagation();
          catObj.collapsed = !catObj.collapsed;
          renderCategories();
          apiCall('PATCH', '/api/categories/' + encodeURIComponent(catObj.id),
            { collapsed: catObj.collapsed }).catch(() => {});
          return;
        }
        // 点行内操作按钮时不切换视图
        if (e.target.closest('.group-op')) return;

        state.activeCat = g.id;
        state.selected.clear();
        render();
      });

      // 拖拽落点（仅分类组）
      if (isCat) {
        el.addEventListener('dragover', e => {
          e.preventDefault();
          el.classList.add('drop-target');
        });
        el.addEventListener('dragleave', () => el.classList.remove('drop-target'));
        el.addEventListener('drop', e => {
          e.preventDefault();
          el.classList.remove('drop-target');
          const id = e.dataTransfer.getData('text/plain');
          if (id) assignCategory([id], g.id);
        });
      }

      box.appendChild(el);
    });

    $('#catTotalCount').textContent = sidebarTotal();
  }

  function statusTag(it) {
    if (it.status === 'dup') return '<span class="tag tag-dup">重复</span>';
    if (it.status === 'container') return '<span class="tag tag-container">容器包</span>';
    if (it.status === 'unverified') return '<span class="tag tag-unver">待确认</span>';
    if (it.status === 'error') return '<span class="tag tag-unver">解析失败</span>';
    if (it.ai_suspect) return '<span class="tag tag-ai">AI 推测</span>';
    return '<span class="tag tag-normal">正常</span>';
  }

  // 类型名压缩显示
  function shortType(t) {
    if (!t) return '—';
    if (t.includes('铁路')) return '铁路客票';
    if (t.includes('增值税') && t.includes('专用')) return '增值税专票';
    if (t.includes('增值税')) return '增值税发票';
    if (t.includes('普通')) return '电子普票';
    if (t.includes('容器')) return '容器包';
    if (t.includes('图片')) return '图片';
    if (t.includes('失败')) return '解析失败';
    return '电子发票';
  }

  function renderTable() {
    const list = viewItems();
    const body = $('#tableBody');
    body.innerHTML = '';

    // 视图标题
    let title = '全部发票';
    if (state.activeCat === '__uncat__') title = '待归类';
    else if (state.activeCat !== '__all__') {
      const c = state.categories.find(x => x.id === state.activeCat);
      title = c ? c.name : '全部发票';
    }
    $('#viewTitle').textContent = title;
    $('#viewMeta').textContent = `${list.length} 个文件`;

    if (!list.length) {
      $('#emptyState').style.display = 'flex';
    } else {
      $('#emptyState').style.display = 'none';
    }

    const frag = document.createDocumentFragment();

    list.forEach((it, i) => {
      const id = itemId(it);
      const tr = document.createElement('tr');
      tr.dataset.id = id;
      if (state.selected.has(id)) tr.classList.add('selected');
      tr.draggable = true;

      const amtCls = it.status === 'unverified' ? 'num-err'
        : (isCounted(it) ? '' : 'num-warn');
      const amtTxt = it.amount == null ? '—' : money(it.amount);

      tr.innerHTML = `
        <td class="col-check"><input type="checkbox" class="cb row-cb" ${state.selected.has(id) ? 'checked' : ''}></td>
        <td class="col-idx">${i + 1}</td>
        <td class="col-name"><span class="fname" title="${esc(it.filename)}">${esc(it.filename)}</span></td>
        <td class="col-amount ${amtCls}">${amtTxt}</td>
        <td class="col-no">${esc(it.invoice_no || '—')}</td>
        <td class="col-seller" title="${esc(it.seller || '')}">${esc(it.seller || '—')}</td>
        <td class="col-date">${esc(it.issue_date || '—')}</td>
        <td class="col-item" title="${esc(it.item || '')}">${esc(it.item || '—')}</td>
        <td class="col-type">${esc(shortType(it.inv_type))}</td>
        <td class="col-type">${statusTag(it)}</td>
      `;

      // 勾选
      const cb = $('.row-cb', tr);
      cb.addEventListener('change', () => {
        if (cb.checked) state.selected.add(id);
        else state.selected.delete(id);
        tr.classList.toggle('selected', cb.checked);
        syncCheckAll();
        renderActionBar();
      });

      // 拖拽开始
      tr.addEventListener('dragstart', e => {
        // 若拖的行已选中，则带上整批；否则只拖这一行
        const ids = state.selected.has(id) ? Array.from(state.selected) : [id];
        e.dataTransfer.setData('text/plain', JSON.stringify(ids));
        e.dataTransfer.effectAllowed = 'move';
      });

      frag.appendChild(tr);
    });

    body.appendChild(frag);

    // 合计
    const counted = list.filter(isCounted);
    const total = counted.reduce((s, it) => s + (it.amount || 0), 0);
    $('#viewTotal').textContent = money(total);
    $('#viewCount').textContent = `（${counted.length} 张）`;

    syncCheckAll();
  }

  /**
   * 同步表头「全选」复选框的三态。
   * 只以「可计账项」为基准——重复项和容器包本来就不可选，
   * 若把它们算进分母，永远凑不满，复选框会卡在半选态。
   */
  function syncCheckAll() {
    const selectable = viewItems().filter(isCounted);
    const all = $('#checkAll');
    const total = selectable.length;
    const sel = selectable.filter(it => state.selected.has(itemId(it))).length;

    all.checked = total > 0 && sel === total;
    all.indeterminate = sel > 0 && sel < total;
    all.disabled = total === 0;
  }

  /** 当前视图是否已全选（用于按钮文字切换） */
  function isViewAllSelected() {
    const selectable = viewItems().filter(isCounted);
    return selectable.length > 0 &&
           selectable.every(it => state.selected.has(itemId(it)));
  }

  function renderActionBar() {
    const n = state.selected.size;
    $('#selInfo').textContent = n ? `已选择 ${n} 张` : '未选择';
    $('#btnMove').disabled = n === 0;
    $('#btnDelete').disabled = n === 0;
    $('#btnArchive').disabled = n === 0;

    // 统计选中金额
    if (n) {
      const sum = state.items
        .filter(it => state.selected.has(itemId(it)) && isCounted(it))
        .reduce((s, it) => s + (it.amount || 0), 0);
      $('#selInfo').textContent = `已选择 ${n} 张 · ${money(sum)}`;
    }

    updateCategoryLabel();

    // 「全选本组」按钮：当前视图已全选时，自动变成「全不选」
    const selBtn = $('#btnSelectGroup');
    if (selBtn) {
      const allSel = isViewAllSelected();
      selBtn.textContent = allSel ? '全不选' : '全选本组';
      selBtn.title = allSel
        ? '取消本组全部选择'
        : '选中本组内全部可计账发票';
    }
  }

  /**
   * 分类按钮的文字跟随「选中发票所属分类」变化：
   *  · 未选中            → 分类选择
   *  · 选中项同属一个分类 → 直接显示该分类名（带颜色圆点）
   *  · 选中项跨多个分类   → 显示“多个分类”
   *  · 选中项均未分类     → 显示“待归类”
   */
  function updateCategoryLabel() {
    const labelEl = $('#moveLabel');
    const dotEl = $('#moveDot');
    if (!labelEl) return;

    const picked = state.items.filter(it => state.selected.has(itemId(it)));
    const cats = [...new Set(picked.map(it => it.category || '__uncat__'))];

    let text = '分类选择';
    let color = null;

    if (picked.length) {
      if (cats.length === 1) {
        const cid = cats[0];
        if (cid === '__uncat__') {
          text = '待归类';
        } else {
          const c = state.categories.find(x => x.id === cid);
          if (c) {
            text = c.name;
            color = c.color;
          }
        }
      } else {
        text = `多个分类（${cats.length}）`;
      }
    }

    labelEl.textContent = text;
    $('#btnMove').title = picked.length
      ? `当前选中 ${picked.length} 张，分类：${text}`
      : '请先选择发票';

    // 圆点：有明确分类才显示
    if (dotEl) {
      if (color) {
        dotEl.hidden = false;
        dotEl.className = 'cat-dot c-' + safeColor(color);
      } else {
        dotEl.hidden = true;
      }
    }

    // 已分类状态下按钮加高亮，与「未分类」区分开
    $('#btnMove').classList.toggle('btn-cat-set', !!color);
  }

  // ---------------------------------------------------------- 分类操作
  function assignCategory(ids, catId) {
    if (!ids.length) return;
    const cat = state.categories.find(c => c.id === catId);
    // 先本地更新，界面立刻响应；后端失败会弹出提示并由 refresh 纠正
    ids.forEach(id => {
      const it = state.items.find(x => itemId(x) === id);
      if (it) it.category = catId;
    });
    toast(catId
      ? `已将 ${ids.length} 张发票归入「${cat ? cat.name : catId}」`
      : `已移出分类：${ids.length} 张`);
    render();

    apiCall('POST', '/api/categories/assign', {
      rel_paths: ids,
      category: catId || null,
    }).then(d => {
      if (!d) {                       // 失败：回滚到服务端真实状态
        Promise.all([loadCategories(), loadData()]);
      }
    });
  }

  // ---------------------------------------------------------- 菜单
  function closeMenu() {
    if (state.menuEl) {
      state.menuEl.remove();
      state.menuEl = null;
    }
  }

  function showMenu(anchor, html, onClick) {
    closeMenu();
    const m = document.createElement('div');
    m.className = 'menu';
    m.innerHTML = html;
    document.body.appendChild(m);

    const r = anchor.getBoundingClientRect();
    const mw = m.offsetWidth, mh = m.offsetHeight;
    let left = r.right - mw;
    let top = r.bottom + 6;
    if (left < 8) left = 8;
    if (top + mh > window.innerHeight - 8) top = r.top - mh - 6;
    m.style.left = left + 'px';
    m.style.top = top + 'px';

    m.addEventListener('click', e => {
      const item = e.target.closest('.menu-item');
      if (!item) return;
      onClick(item.dataset.act);
      closeMenu();
    });

    state.menuEl = m;
    setTimeout(() => {
      document.addEventListener('click', closeMenu, { once: true });
    }, 0);
  }

  // ---------------------------------------------------------- 事件
  function bindEvents() {
    // 搜索
    $('#searchBox').addEventListener('input', e => {
      state.keyword = e.target.value;
      renderTable();
    });

    // 排序
    $$('thead th.sortable').forEach(th => {
      th.addEventListener('click', () => {
        const k = th.dataset.sort;
        if (state.sortKey === k) state.sortDir *= -1;
        else { state.sortKey = k; state.sortDir = 1; }
        $$('thead th').forEach(x => x.classList.remove('sorted'));
        th.classList.add('sorted');
        renderTable();
      });
    });

    // 表头全选：已是全选则整体取消，否则全选（仅可计账项）
    $('#checkAll').addEventListener('change', e => {
      const selectable = viewItems().filter(isCounted);
      const wasAll = selectable.length > 0 &&
                     selectable.every(it => state.selected.has(itemId(it)));
      // 半选态点击也视为“全选”，避免用户点一下没反应
      const shouldSelect = !wasAll;

      if (shouldSelect) {
        selectable.forEach(it => state.selected.add(itemId(it)));
      } else {
        viewItems().forEach(it => state.selected.delete(itemId(it)));
      }
      renderTable();
      renderActionBar();
    });

    // 全选本组 / 全不选：按当前状态自动切换文案与行为
    $('#btnSelectGroup').addEventListener('click', () => {
      const selectable = viewItems().filter(isCounted);
      if (isViewAllSelected()) {
        // 全不选：清掉本视图内的选择
        viewItems().forEach(it => state.selected.delete(itemId(it)));
      } else {
        selectable.forEach(it => state.selected.add(itemId(it)));
      }
      renderTable();
      renderActionBar();
    });

    // 分类选择
    $('#btnMove').addEventListener('click', e => {
      if (!state.selected.size) return;

      // 当前选中项所属分类（用于打勾）
      const picked = state.items.filter(it => state.selected.has(itemId(it)));
      const cur = [...new Set(picked.map(it => it.category || '__uncat__'))];
      const curOne = cur.length === 1 ? cur[0] : null;

      const tick = cid => (curOne === cid
        ? `<svg class="menu-tick" width="13" height="13" viewBox="0 0 16 16" fill="none"
                stroke="currentColor" stroke-width="2">
             <path d="M3.5 8.5 6.5 11.5 12.5 4.5" stroke-linecap="round" stroke-linejoin="round"/>
           </svg>`
        : '');

      const rows = state.categories.map(c =>
        `<button class="menu-item" data-act="${c.id}">
           <span class="group-dot c-${safeColor(c.color)}"></span>
           <span class="menu-text">${esc(c.name)}</span>
           ${tick(c.id)}
         </button>`).join('');

      showMenu(e.currentTarget,
        `<div class="menu-label">选择分类</div>${rows}
         <div class="menu-sep"></div>
         <button class="menu-item" data-act="__none__">
           <span class="group-dot"></span>
           <span class="menu-text">移出分类（待归类）</span>
           ${tick('__uncat__')}
         </button>`,
        act => {
          const ids = Array.from(state.selected);
          if (act === '__none__') {
            assignCategory(ids, null);
          } else {
            assignCategory(ids, act);
          }
        });
    });

    // 删除发票
    $('#btnDelete').addEventListener('click', () => {
      const ids = Array.from(state.selected);
      if (!ids.length) return;

      const targets = ids
        .map(id => state.items.find(x => itemId(x) === id))
        .filter(Boolean);
      const counted = targets.filter(isCounted);
      const sum = counted.reduce((s, it) => s + (it.amount || 0), 0);

      const preview = targets.slice(0, 5).map(it => `· ${it.filename}`).join('\n');
      const more = targets.length > 5 ? `\n… 等共 ${targets.length} 个文件` : '';
      const ok = confirm(
        `把以下 ${targets.length} 个发票从当前列表移除？\n\n${preview}${more}\n\n` +
        (counted.length ? `其中 ${counted.length} 张计账发票，合计 ${money(sum)}\n\n` : '') +
        `注意：只在本次列表中隐藏，不会删除磁盘上的文件；` +
        `重新扫描后它们会再次出现。`
      );
      if (!ok) return;

      // 从数据集中移除
      const idSet = new Set(ids);
      state.items = state.items.filter(it => !idSet.has(itemId(it)));
      ids.forEach(id => state.selected.delete(id));

      toast(`已从列表移除 ${targets.length} 项（磁盘文件未改动）`, 3200);
      render();
    });

    // 执行归档
    $('#btnArchive').addEventListener('click', () => {
      const ids = Array.from(state.selected);
      const withCat = ids.filter(id => {
        const it = state.items.find(x => itemId(x) === id);
        return it && it.category;
      });
      if (!withCat.length) {
        toast('所选发票都还没有分类，请先「分类选择」指定分类');
        return;
      }
      const names = [...new Set(withCat.map(id => {
        const it = state.items.find(x => itemId(x) === id);
        const c = state.categories.find(x => x.id === it.category);
        return c ? c.name : '';
      }))].filter(Boolean);
      toast(`归档 ${withCat.length} 张 → ${names.join('、')}（演示模式，未真正移动文件）`, 3200);
    });

    // 更换发票文件夹
    $('#btnPickFolder').addEventListener('click', e => {
      showMenu(e.currentTarget,
        `<div class="menu-label">发票文件夹</div>
         <button class="menu-item" data-act="pick">
           <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
             <path d="M1.8 4.6A1 1 0 0 1 2.8 3.6h2.9l1.2 1.4h6.3a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1z"
                   stroke-linejoin="round"/>
           </svg>
           选择其他文件夹…
         </button>
         <button class="menu-item" data-act="open">
           <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
             <path d="M6 2.5H3.5a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1V10M9.5 2.5H13v3.5M13 2.5 7.5 8"
                   stroke-linecap="round" stroke-linejoin="round"/>
           </svg>
           在资源管理器中打开
         </button>
         <div class="menu-sep"></div>
         <button class="menu-item" data-act="copy">
           <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
             <rect x="5.5" y="5.5" width="8" height="8" rx="1"/>
             <path d="M10.5 5.5v-2a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2" stroke-linecap="round"/>
           </svg>
           复制路径
         </button>`,
        act => {
          const cur = $('#rootPath').textContent;
          if (act === 'copy') {
            navigator.clipboard.writeText(cur).then(
              () => toast('路径已复制'),
              () => toast('复制失败，请手动选择文本')
            );
          } else if (act === 'pick') {
            // 演示版：真实版会调用后端目录选择框
            toast('演示模式：正式版将弹出系统文件夹选择框', 2800);
          } else if (act === 'open') {
            toast('演示模式：正式版将在资源管理器中打开该文件夹', 2800);
          }
        });
    });

    // 设置面板
    const API_ROUTES = [
      ['GET', '/api/health', '健康检查'],
      ['GET', '/api/summary', '汇总统计'],
      ['GET', '/api/invoices', '发票列表（可筛选）'],
      ['GET', '/api/invoice/{id}', '单张详情'],
      ['GET', '/api/invoice/{id}/text', '原文（供 AI 分析）'],
      ['GET', '/api/rules', '修复规则列表'],
      ['POST', '/api/rules', '新增修复规则'],
      ['DELETE', '/api/rules/{id}', '删除规则'],
    ];

    function openSettings() {
      const mask = $('#settingsMask');
      mask.hidden = false;

      // 地址
      $('#apiAddr').textContent = location.origin;

      // 令牌 + MCP 配置（走 bootstrap，免令牌，仅回环）
      fetch('/api/bootstrap')
        .then(r => r.json())
        .then(cfg => {
          if (cfg.token) {
            state.apiToken = cfg.token;
            $('#apiToken').textContent = cfg.token;
            $('#apiToken').dataset.token = cfg.token;
          } else {
            $('#apiToken').textContent = '（点「重置」生成）';
          }
          // 用真实路径填充 MCP 配置，用户直接复制即可
          if (cfg.mcp) {
            const snippet = JSON.stringify({
              mcpServers: {
                'invoice-assistant': {
                  command: cfg.mcp.command,
                  args: [cfg.mcp.script],
                },
              },
            }, null, 2);
            $('#mcpConfig').textContent = snippet;
          }
        })
        .catch(() => { $('#apiToken').textContent = '（读取失败）'; });

      // 接口清单
      const list = $('#apiList');
      if (!list.children.length) {
        list.innerHTML = API_ROUTES.map(([m, p, d]) => {
          const cls = m === 'GET' ? 'get' : (m === 'POST' ? 'post' : 'del');
          return `<div class="api-item">
                    <span class="api-method ${cls}">${m}</span>
                    <span class="api-path">${esc(p)}</span>
                    <span class="api-desc">${esc(d)}</span>
                  </div>`;
        }).join('');
      }
    }

    $('#btnSettings').addEventListener('click', openSettings);
    $('#btnCloseSettings').addEventListener('click', () => { $('#settingsMask').hidden = true; });
    $('#settingsMask').addEventListener('click', e => {
      if (e.target.id === 'settingsMask') e.target.hidden = true;
    });

    $('#btnCopyAddr').addEventListener('click', () => {
      navigator.clipboard.writeText($('#apiAddr').textContent)
        .then(() => toast('地址已复制'), () => toast('复制失败'));
    });

    $('#btnCopyToken').addEventListener('click', () => {
      const t = $('#apiToken').dataset.token || '';
      if (!t) { toast('请先点「重置」生成令牌'); return; }
      navigator.clipboard.writeText(t)
        .then(() => toast('令牌已复制，可直接粘贴给 AI 客户端'), () => toast('复制失败'));
    });

    $('#btnResetToken').addEventListener('click', () => {
      if (!confirm('重置令牌后，所有已配置的 AI 客户端都需要更新令牌。确定继续？')) return;
      fetch('/api/config/token/reset', {
        method: 'POST',
        headers: { 'X-Api-Token': state.apiToken || '' },
      })
        .then(r => r.json())
        .then(d => {
          if (d.token) {
            state.apiToken = d.token;
            $('#apiToken').textContent = d.token;
            $('#apiToken').dataset.token = d.token;
            toast('令牌已重置');
          } else {
            toast('重置失败');
          }
        })
        .catch(() => toast('重置失败'));
    });

    $('#btnCopyMcp').addEventListener('click', () => {
      navigator.clipboard.writeText($('#mcpConfig').textContent)
        .then(() => toast('MCP 配置已复制'), () => toast('复制失败'));
    });

    // Esc 关闭设置
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && !$('#settingsMask').hidden) $('#settingsMask').hidden = true;
    });

    // 新建分类
    $('#btnAddCat').addEventListener('click', () => {
      const name = prompt('新分类名称（将作为文件夹名）');
      if (!name) return;
      const nm = name.trim();
      if (!nm) return;
      if (state.categories.some(c => c.name === nm)) {
        toast('该分类已存在');
        return;
      }
      apiCall('POST', '/api/categories', { name: nm }).then(d => {
        if (!d || !d.category) return;
        state.categories.push(d.category);
        toast(`已新建分类「${nm}」`);
        render();
      });
    });

    // 重新扫描（异步 + 轮询进度）
    $('#btnRescan').addEventListener('click', () => {
      const btn = $('#btnRescan');
      const old = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '扫描中…';

      const stop = () => {
        btn.disabled = false;
        btn.innerHTML = old;
      };

      ensureToken()
        .then(tok => fetch('/api/rescan', {
          method: 'POST',
          headers: { 'X-Api-Token': tok },
        }))
        .then(r => r.json())
        .then(d => {
          if (d.error) { toast('扫描失败：' + d.error); stop(); return; }

          // 轮询状态
          let ticks = 0;
          const timer = setInterval(() => {
            ticks++;
            fetch('/api/scan/status', {
              headers: { 'X-Api-Token': state.apiToken || '' },
            })
              .then(r => r.json())
              .then(st => {
                if (st.running) {
                  btn.innerHTML = `扫描中… ${Math.round(ticks * 1.5)}s`;
                  return;
                }
                clearInterval(timer);
                stop();
                if (st.error) { toast('扫描失败：' + st.error); return; }
                const res = st.result || {};
                toast(`扫描完成：${res.files} 个文件，计账 ${res.counted} 张，¥${(res.total || 0).toFixed(2)}`, 3400);
                loadData({ keepCategories: true });
              })
              .catch(() => { clearInterval(timer); stop(); toast('扫描状态查询失败'); });
          }, 1500);
        })
        .catch(() => { toast('扫描启动失败'); stop(); });
    });

    // 导出 Excel
    $('#btnExport').addEventListener('click', () => {
      // 把当前分类分配一并带给后端，导出的表里能看到分类
      const catMap = {};
      state.items.forEach(it => {
        if (it.category) {
          const c = state.categories.find(x => x.id === it.category);
          catMap[itemId(it)] = c ? c.name : it.category;
        }
      });

      const payload = {
        label: $('#viewTitle').textContent,
        categories: catMap,
      };

      const btn = $('#btnExport');
      const old = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '导出中…';

      ensureToken()
        .then(tok => fetch('/api/export', {
          method: 'POST',
          headers: {
            'X-Api-Token': tok,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(payload),
        }))
        .then(r => r.json())
        .then(d => {
          if (d.error) {
            toast('导出失败：' + d.error);
            return;
          }
          toast(`已导出：${d.counted} 张 / ¥${d.total.toFixed(2)}\n${d.path}`, 4200);
        })
        .catch(() => toast('导出失败'))
        .finally(() => {
          btn.disabled = false;
          btn.innerHTML = old;
        });
    });

    // Esc 关闭菜单
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') closeMenu();
    });
  }

  // ---------------------------------------------------------- 心跳
  // 页面存活期间定时 ping，服务端据此判断“页面已关闭”并自动退出
  function startHeartbeat() {
    const ping = () => {
      fetch('/api/ping', { cache: 'no-store' }).catch(() => {});
    };
    ping();
    setInterval(ping, 3000);
  }

  // 启动
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { init(); startHeartbeat(); });
  } else {
    init();
    startHeartbeat();
  }

})();
