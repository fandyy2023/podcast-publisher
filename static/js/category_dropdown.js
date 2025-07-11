// category_dropdown.js
// Автоматическое заполнение категорий и подкатегорий Apple Podcasts
// Требует: window.APPLE_CATEGORIES

function populateCategories(mainSel, subSel, currentMain, currentSub) {
    mainSel.innerHTML = '';
    Object.keys(window.APPLE_CATEGORIES).forEach(function(cat) {
        let opt = document.createElement('option');
        opt.value = cat;
        opt.textContent = cat;
        if (cat === currentMain) opt.selected = true;
        mainSel.appendChild(opt);
    });
    function updateSubcats() {
        let mainVal = mainSel.value;
        let subcats = window.APPLE_CATEGORIES[mainVal] || [];
        subSel.innerHTML = '';
        if (subcats.length === 0) {
            subSel.disabled = true;
            let opt = document.createElement('option');
            opt.value = '';
            opt.textContent = '(нет подкатегорий)';
            subSel.appendChild(opt);
        } else {
            subSel.disabled = false;
            let none = document.createElement('option');
            none.value = '';
            none.textContent = '(без подкатегории)';
            subSel.appendChild(none);
            subcats.forEach(function(sub) {
                let opt = document.createElement('option');
                opt.value = sub;
                opt.textContent = sub;
                if (sub === currentSub) opt.selected = true;
                subSel.appendChild(opt);
            });
        }
    }
    mainSel.addEventListener('change', function() {
        currentSub = '';
        updateSubcats();
    });
    updateSubcats();
}

// Автоматически инициализировать на всех страницах
window.addEventListener('DOMContentLoaded', function() {
    console.log('[category_dropdown.js] loaded', {
        APPLE_CATEGORIES: window.APPLE_CATEGORIES,
        main: document.getElementById('category_main'),
        sub: document.getElementById('category_sub')
    });
    let mainSel = document.getElementById('category_main');
    let subSel = document.getElementById('category_sub');
    if (!mainSel || !subSel || !window.APPLE_CATEGORIES) return;
    let currentMain = mainSel.getAttribute('data-current') || '';
    let currentSub = subSel.getAttribute('data-current') || '';
    populateCategories(mainSel, subSel, currentMain, currentSub);
});
