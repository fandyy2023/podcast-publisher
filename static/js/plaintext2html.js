/**
 * Преобразует обычный текст с переносами строк и списками в HTML с сохранением структуры:
 * - Нумерованные списки (1., 2., 3., ...) → <ol><li>...
 * - Маркированные списки (•, -, *) → <ul><li>...
 * - Абзацы → <p>...
 */
window.plainTextToHtml = function(text) {
    if (!text) return '';
    
    // Разделяем на строки
    let lines = text.split(/\r?\n/);
    let html = '';
    let inUl = false, inOl = false;
    
    for (let i = 0; i < lines.length; ++i) {
        let line = lines[i].trimEnd();
        
        if (!line) {
            // Пустая строка - закрываем списки если они открыты
            if (inUl) { html += '</ul>'; inUl = false; }
            if (inOl) { html += '</ol>'; inOl = false; }
            continue;
        }
        
        if (/^\d+\.\s+/.test(line)) {
            // Нумерованный список
            if (!inOl) { html += '<ol>'; inOl = true; }
            if (inUl) { html += '</ul>'; inUl = false; }
            html += '<li>' + line.replace(/^\d+\.\s+/, '') + '</li>';
        } else if (/^([\u2022\-\*]|•)\s+/.test(line)) {
            // Маркированный список
            if (!inUl) { html += '<ul>'; inUl = true; }
            if (inOl) { html += '</ol>'; inOl = false; }
            html += '<li>' + line.replace(/^([\u2022\-\*]|•)\s+/, '') + '</li>';
        } else {
            // Обычный текст - абзац
            if (inUl) { html += '</ul>'; inUl = false; }
            if (inOl) { html += '</ol>'; inOl = false; }
            html += '<p>' + line + '</p>';
        }
    }
    
    // Закрываем открытые списки
    if (inUl) html += '</ul>';
    if (inOl) html += '</ol>';
    
    return html;
};
