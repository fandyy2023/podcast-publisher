// Live preview очистки описания/summary для подкастов (Spotify/Apple)
// Использует fetch к backend-эндпоинту /sanitize_html для получения очищенного HTML

async function updatePreview(inputId, previewId) {
    const raw = document.getElementById(inputId).value;
    const preview = document.getElementById(previewId);
    if (!preview) return;
    if (!raw.trim()) {
        preview.innerHTML = '<em>Нет текста для предпросмотра</em>';
        return;
    }
    try {
        const resp = await fetch('/sanitize_html', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ html: raw })
        });
        if (resp.ok) {
            const data = await resp.json();
            preview.innerHTML = data.cleaned || '<em>Ошибка очистки</em>';
        } else {
            preview.innerHTML = '<em>Ошибка сервера</em>';
        }
    } catch (e) {
        preview.innerHTML = '<em>Ошибка соединения</em>';
    }
}

function setupLivePreview(inputId, previewId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    input.addEventListener('input', () => updatePreview(inputId, previewId));
    input.addEventListener('paste', () => setTimeout(() => updatePreview(inputId, previewId), 0));
    // Первичная инициализация
    updatePreview(inputId, previewId);
}
