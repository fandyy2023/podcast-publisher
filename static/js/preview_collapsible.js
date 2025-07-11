// Делает все предпросмотры сворачиваемыми по клику на заголовок
function setupCollapsiblePreviews() {
  document.querySelectorAll('.preview-toggle').forEach(function(toggle) {
    const preview = toggle.nextElementSibling;
    toggle.addEventListener('click', function() {
      preview.classList.toggle('open');
      toggle.classList.toggle('open');
    });
  });
}

document.addEventListener('DOMContentLoaded', setupCollapsiblePreviews);
