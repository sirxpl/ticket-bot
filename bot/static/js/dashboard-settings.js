(() => {
    'use strict';
    const roots = [...document.querySelectorAll('#page-access,#page-carry-settings,#page-carry-applications,#page-ticket-logs,#page-trial-schedules')];
    const notice = document.getElementById('dashboard-unsaved-notice');
    const baselines = new WeakMap();
    let forms = [];
    let ready = false;
    let scheduled = false;
    const ignored = element => element.matches('[data-unsaved-ignore]') || element.closest('[data-unsaved-ignore]');
    const capture = form => JSON.stringify([...form.elements].filter(element =>
        element.matches('input,select,textarea') && !element.disabled && !element.readOnly &&
        !['submit','button','reset','search'].includes(element.type) && element.name !== 'csrf_token' && !ignored(element)
    ).map((element, index) => [element.name || element.id || String(index), element.type,
        ['checkbox','radio'].includes(element.type) ? [element.checked, element.value] :
        element.multiple ? [...element.selectedOptions].map(option => option.value) : element.value]));
    const check = () => {
        scheduled = false;
        if (!ready || !notice) return;
        const dirty = forms.filter(form => form.isConnected && capture(form) !== baselines.get(form));
        notice.hidden = dirty.length === 0;
        document.body.classList.toggle('dashboard-dirty', dirty.length > 0);
        const sections = new Set(dirty.map(form => form.closest('details') || form));
        const text = `${sections.size} edited section${sections.size === 1 ? '' : 's'}. Save each section when you’re ready.`;
        if (notice.querySelector('small').textContent !== text) notice.querySelector('small').textContent = text;
    };
    const schedule = () => {
        if (scheduled || !ready) return;
        scheduled = true;
        requestAnimationFrame(check);
    };
    window.DashboardUnsaved = {
        capture,
        markSaved(form, snapshot) {
            if (snapshot !== undefined) baselines.set(form, snapshot);
            schedule();
        },
        refresh: schedule,
    };
    const initialize = () => requestAnimationFrame(() => {
        forms = roots.flatMap(root => [...root.querySelectorAll('form')]).filter(form =>
            form.method.toLowerCase() === 'post' && !form.matches('[data-unsaved-ignore],[data-application-selection]')
        );
        forms.forEach(form => baselines.set(form, capture(form)));
        ready = true;
        ['input','change','click','drop','dragend','reset'].forEach(event => document.addEventListener(event, schedule, true));
        roots.forEach(root => new MutationObserver(schedule).observe(root, {childList: true, subtree: true}));
        check();
    });
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, {once: true});
    else initialize();
    const updateBulk = form => {
        const boxes = [...form.elements].filter(element => element.matches('[data-bulk-item]'));
        const chosen = boxes.filter(box => box.checked);
        const all = form.querySelector('[data-bulk-all]');
        all.checked = boxes.length > 0 && chosen.length === boxes.length;
        all.indeterminate = chosen.length > 0 && chosen.length < boxes.length;
        all.disabled = boxes.length === 0;
        form.querySelector('[data-bulk-count]').textContent = `${chosen.length} selected`;
        form.querySelector('[data-bulk-submit]').disabled = chosen.length === 0;
    };
    document.querySelectorAll('[data-bulk-form]').forEach(form => {
        form.querySelector('[data-bulk-all]').addEventListener('change', event => {
            [...form.elements].filter(element => element.matches('[data-bulk-item]')).forEach(box => { box.checked = event.target.checked; });
            updateBulk(form);
        });
        updateBulk(form);
    });
    document.addEventListener('change', event => {
        if (event.target.matches('[data-bulk-item]') && event.target.form) updateBulk(event.target.form);
    });
    const categories = document.getElementById('categoriesForm');
    if (categories) {
        const error = document.getElementById('category-bulk-error');
        categories.querySelector('[data-select-all-categories]')?.addEventListener('change', event => {
            categories.querySelectorAll('.category-bulk-check').forEach(box => { box.checked = event.target.checked; });
        });
        categories.querySelector('[data-remove-selected-categories]')?.addEventListener('click', () => {
            const rows = [...categories.querySelectorAll('#categoriesContainer>.embed-field-row')];
            const chosen = rows.filter(row => row.querySelector('.category-bulk-check')?.checked);
            if (!chosen.length || chosen.length === rows.length) {
                error.textContent = chosen.length ? 'Keep at least one category.' : 'Select the categories you want to remove.';
                error.hidden = false;
                return;
            }
            error.hidden = true;
            chosen.forEach(row => row.remove());
            categories.querySelector('[data-select-all-categories]').checked = false;
            window.saveCategoriesNow();
            schedule();
        });
        categories.addEventListener('click', event => {
            const button = event.target.closest('[data-remove-category-chips]');
            if (!button) return;
            const row = button.closest('.embed-field-row');
            const kind = button.dataset.removeCategoryChips;
            const chosen = [...row.querySelectorAll(`.category-chip-check[data-chip-kind="${kind}"]:checked`)];
            if (!chosen.length) return;
            const hidden = row.querySelector(kind === 'roles' ? '.cat-blacklist-hidden' : '.cat-badge-hidden');
            const ids = new Set(chosen.map(box => box.value));
            hidden.value = hidden.value.split(',').filter(value => !ids.has(value)).join(',');
            chosen.forEach(box => box.parentElement.remove());
            window.saveCategoriesNow();
            schedule();
        });
    }
    const anchor = document.getElementById(location.hash.slice(1));
    if (anchor?.matches('details')) anchor.open = true;
})();


(() => {
    const form = document.getElementById('blacklist-types-form');
    if (!form) return;
    const fields = document.getElementById('blacklist-type-fields');
    const error = document.getElementById('blacklist-fields-error');
    document.getElementById('add-blacklist-type').addEventListener('click', () => {
        if (fields.children.length >= 25) {
            error.textContent = 'Use at most 25 blacklist types.';
            error.hidden = false;
            return;
        }
        fields.append(document.getElementById('blacklist-type-template').content.cloneNode(true));
        fields.lastElementChild.querySelector('input').focus();
        window.DashboardUnsaved?.refresh();
    });
    fields.addEventListener('click', event => {
        if (!event.target.closest('[data-remove-blacklist-type]')) return;
        event.target.closest('.blacklist-type-row').remove();
        error.hidden = true;
        window.DashboardUnsaved?.refresh();
    });
})();
