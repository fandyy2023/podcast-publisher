/**
 * Преобразует HTML с <p>, <ul>, <ol>, <li>, <br> обратно в plain text с переносами строк и списками.
 * Используется для обратного преобразования description при открытии inline-редактора.
 */
window.htmlToPlainText = function(html) {
    if (!html) return '';
    
    // Замена тегов на их текстовые эквиваленты
    let text = html
        .replace(/<br\s*\/?>/gi, '\n')
        .replace(/<\/p>|<\/li>|<\/ul>|<\/ol>/gi, '\n')
        .replace(/<li>/gi, '\n• ')
        .replace(/<[^>]+>/g, ''); // Удаление всех оставшихся тегов
    
    // Декодирование HTML-сущностей
    let textarea = document.createElement('textarea');
    textarea.innerHTML = text;
    text = textarea.value;
    
    // Удаление лишних переносов строк
    text = text.replace(/\n{3,}/g, '\n\n');
    
    return text.trim();
};
