// Автоматическое определение длительности аудиофайла и запись в поле duration
// Подключать к new_episode.html и edit_episode.html

document.addEventListener('DOMContentLoaded', function() {
  const audioInput = document.getElementById('audio');
  const durationInput = document.getElementById('duration');
  if (!audioInput || !durationInput) return;

  audioInput.addEventListener('change', function() {
    const file = audioInput.files && audioInput.files[0];
    if (!file) return;
    const url = URL.createObjectURL(file);
    const audio = new Audio();
    audio.preload = 'metadata';
    audio.src = url;
    audio.onloadedmetadata = function() {
      if (audio.duration && isFinite(audio.duration)) {
        // Форматируем как HH:MM:SS
        const h = Math.floor(audio.duration / 3600);
        const m = Math.floor((audio.duration % 3600) / 60);
        const s = Math.round(audio.duration % 60);
        const pad = n => n.toString().padStart(2, '0');
        let formatted = '';
        if (h > 0) formatted += pad(h) + ':';
        formatted += pad(m) + ':' + pad(s);
        durationInput.value = formatted;
      }
      URL.revokeObjectURL(url);
    };
    audio.onerror = function() {
      durationInput.value = '';
      URL.revokeObjectURL(url);
    };
  });
});
