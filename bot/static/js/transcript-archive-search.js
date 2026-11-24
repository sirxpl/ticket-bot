(() => {
    'use strict';
    const field = document.getElementById('transcript-global-search');
    const list = document.getElementById('transcript-list');
    const status = document.getElementById('transcript-search-status');
    const pagination = document.getElementById('transcript-pagination');
    const paginationLabel = document.getElementById('transcript-pagination-label');
    if (!field || !list || !field.dataset.searchUrl) return;
    const endpoint = field.dataset.searchUrl;
    const originalMarkup = list.innerHTML;
    const originalPaginationLabel = paginationLabel ? paginationLabel.textContent : '';
    const originalPaginationDisplay = pagination ? pagination.style.display : '';
    const iconTemplate = list.querySelector('.tr-index-icon');
    let debounceTimer = null;
    let activeController = null;
    let requestNumber = 0;
    function makeEmpty(title, description) {
        const empty = document.createElement('div'); empty.className = 'tr-empty';
        const heading = document.createElement('h3'); heading.textContent = title;
        const copy = document.createElement('p'); copy.textContent = description;
        empty.append(heading, copy); return empty;
    }
    function makeRow(item) {
        const row = document.createElement('div'); row.className = 'tr-index-row searchable-row';
        if (iconTemplate) row.append(iconTemplate.cloneNode(true));
        const info = document.createElement('div'); info.className = 'tr-index-info';
        const title = document.createElement('div'); title.className = 'tr-index-title';
        const ticket = document.createElement('strong'); ticket.textContent = item.ticket_number ? 'Ticket #' + item.ticket_number : 'Ticket archive';
        const closed = document.createElement('span'); closed.className = 'tr-index-closed'; closed.textContent = 'Closed';
        title.append(ticket, closed);
        const person = document.createElement('div'); person.className = 'tr-index-person';
        person.append(document.createTextNode(item.username || 'Unknown member'));
        if (item.user_id) { const userId = document.createElement('span'); userId.textContent = item.user_id; person.append(userId); }
        info.append(title, person);
        const link = document.createElement('a'); link.className = 'tr-index-open'; link.href = item.url;
        link.target = '_blank'; link.rel = 'noopener noreferrer';
        link.setAttribute('aria-label', 'Open transcript ' + (item.ticket_number ? '#' + item.ticket_number : ''));
        link.append(document.createTextNode('Open transcript '));
        const arrow = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        arrow.setAttribute('class', 'tr-icon'); arrow.setAttribute('viewBox', '0 0 24 24');
        arrow.setAttribute('fill', 'none'); arrow.setAttribute('stroke', 'currentColor');
        arrow.setAttribute('stroke-width', '1.6'); arrow.setAttribute('stroke-linecap', 'round');
        arrow.setAttribute('stroke-linejoin', 'round'); arrow.setAttribute('aria-hidden', 'true');
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', 'm14 6-6 6 6 6M8 12h12'); arrow.append(path); link.append(arrow);
        row.append(info, link); return row;
    }
    function restoreArchive() {
        list.innerHTML = originalMarkup;
        if (pagination) pagination.style.display = originalPaginationDisplay;
        if (paginationLabel) paginationLabel.textContent = originalPaginationLabel;
        status.textContent = '';
    }
    async function search(query) {
        const currentRequest = ++requestNumber;
        if (activeController) activeController.abort();
        activeController = new AbortController(); status.textContent = 'Searching…';
        if (pagination) pagination.style.display = 'none';
        try {
            const url = new URL(endpoint, window.location.origin); url.searchParams.set('q', query);
            const response = await fetch(url, {method: 'GET', credentials: 'same-origin', headers: {'Accept': 'application/json'}, signal: activeController.signal});
            if (!response.ok) throw new Error('Search failed (' + response.status + ')');
            const data = await response.json(); if (currentRequest !== requestNumber) return;
            list.replaceChildren();
            const results = Array.isArray(data.results) ? data.results : [];
            if (results.length) results.forEach(item => list.append(makeRow(item)));
            else list.append(makeEmpty('No matching transcripts', 'Try another ticket number, member name, or Discord ID.'));
            const total = Number.isFinite(Number(data.total)) ? Number(data.total) : results.length;
            status.textContent = total.toLocaleString() + ' match' + (total === 1 ? '' : 'es');
            if (paginationLabel) paginationLabel.textContent = 'Showing ' + results.length + ' of ' + total + ' matches';
        } catch (error) {
            if (error.name === 'AbortError' || currentRequest !== requestNumber) return;
            list.replaceChildren(makeEmpty('Search unavailable', 'Please try again in a moment.')); status.textContent = 'Search failed';
        }
    }
    field.addEventListener('input', () => {
        const query = field.value.trim(); window.clearTimeout(debounceTimer);
        if (!query) { requestNumber++; if (activeController) activeController.abort(); restoreArchive(); return; }
        debounceTimer = window.setTimeout(() => search(query), 250);
    });
})();