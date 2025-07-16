// Функция для переключения вкладок
function showTab(tabId) {
    console.log(`Переключение на вкладку: ${tabId}`);
    
    // Скрываем все вкладки
    const tabContents = document.querySelectorAll('.tab-content');
    tabContents.forEach(tab => {
        tab.classList.remove('active');
        tab.style.display = 'none';
    });
    
    // Убираем активность со всех заголовков вкладок
    const tabs = document.querySelectorAll('.tab');
    tabs.forEach(tab => {
        tab.classList.remove('active');
    });
    
    // Показываем текущую вкладку и делаем её заголовок активным
    const currentTab = document.getElementById(tabId + '-tab');
    const currentTabHeader = document.querySelector(`.tab[data-tab="${tabId}"]`);
    
    if (currentTab) {
        currentTab.classList.add('active');
        currentTab.style.display = 'block';
        console.log(`Вкладка ${tabId} показана`);
    } else {
        console.error(`Вкладка с ID ${tabId}-tab не найдена`);
    }
    
    if (currentTabHeader) {
        currentTabHeader.classList.add('active');
    }
}

// Инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', function() {
    console.log('DOM загружен, инициализация вкладок');
    
    // Добавляем обработчики событий для всех вкладок
    const tabs = document.querySelectorAll('.tab');
    tabs.forEach(tab => {
        tab.addEventListener('click', function() {
            const tabId = this.getAttribute('data-tab');
            showTab(tabId);
        });
    });
    
    // По умолчанию показываем первую вкладку (settings)
    // Небольшая задержка для гарантии, что DOM полностью загружен
    setTimeout(() => {
        // Находим первую вкладку или используем 'settings' по умолчанию
        const firstTab = document.querySelector('.tab');
        const firstTabId = firstTab ? firstTab.getAttribute('data-tab') : 'settings';
        showTab(firstTabId);
    }, 100);
});
