// Plain text to HTML (client-side, for inline description rendering)
window.plainTextToHtml = function(text) {
    if (!text) return '';
    // 1. Списки (маркированные и нумерованные)
    let lines = text.split(/\r?\n/);
    let html = '';
    let inUl = false, inOl = false;
    for (let i = 0; i < lines.length; ++i) {
        let line = lines[i].trimEnd();
        if (/^\d+\.\s+/.test(line)) {
            if (!inOl) { html += '<ol>'; inOl = true; }
            if (inUl) { html += '</ul>'; inUl = false; }
            html += '<li>' + line.replace(/^\d+\.\s+/, '') + '</li>';
        } else if (/^([\u2022\-\*]|•)\s+/.test(line)) {
            if (!inUl) { html += '<ul>'; inUl = true; }
            if (inOl) { html += '</ol>'; inOl = false; }
            html += '<li>' + line.replace(/^([\u2022\-\*]|•)\s+/, '') + '</li>';
        } else if (line.length > 0) {
            if (inUl) { html += '</ul>'; inUl = false; }
            if (inOl) { html += '</ol>'; inOl = false; }
            html += '<p>' + line + '</p>';
        }
    }
    if (inUl) html += '</ul>';
    if (inOl) html += '</ol>';
    return html;
};
