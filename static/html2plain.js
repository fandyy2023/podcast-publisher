// HTML to plain text (client-side, для inline-редактирования)
window.htmlToPlainText = function(html) {
    if (!html) return '';
    // Заменяем <br>, </p>, </li>, </ul>, </ol> на \n
    let text = html.replace(/<br\s*\/?>/gi, '\n')
                   .replace(/<\/(p|li|ul|ol)>/gi, '\n')
                   .replace(/<li>/gi, '• ')
                   .replace(/<[^>]+>/g, '');
    text = text.replace(/\n{3,}/g, '\n\n');
    return text.trim();
};
