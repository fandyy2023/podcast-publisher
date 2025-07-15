#!/usr/bin/env python3
"""
Скрипт для создания тестовых Excel-файлов для пакетной загрузки эпизодов.
Создает файлы english.xlsx и russian.xlsx в папке data.
"""

import os
import pandas as pd

def create_english_excel():
    """Create English test Excel file"""
    data = {
        'Number': [1, 2, 3, 4, 5],
        'Title': [
            'Introduction to Podcasting', 
            'Equipment for Beginners', 
            'Recording Techniques', 
            'Editing Your Podcast', 
            'Distribution and Promotion'
        ],
        'Summary': [
            'Learn the basics of podcasting and what you need to start.',
            'Essential equipment for podcast beginners on a budget.',
            'Professional recording techniques for clear audio.',
            'Tips and tricks for editing your podcast efficiently.',
            'How to distribute and promote your podcast to reach more listeners.'
        ],
        'Full description': [
            'In this episode, we cover the fundamentals of podcasting, including planning your content, choosing a format, and understanding your audience.',
            'We discuss microphones, headphones, audio interfaces, and recording software that offer the best value for beginners.',
            'Learn professional techniques for recording clear audio, proper microphone placement, and dealing with room acoustics.',
            'This episode covers editing workflows, software recommendations, and techniques to make your podcast sound professional.',
            'Discover strategies for distributing your podcast across platforms and promoting it effectively on social media.'
        ],
        'Genre': [
            'Education,Technology', 
            'Technology,Equipment', 
            'Education,Audio', 
            'Technology,Production', 
            'Marketing,Business'
        ]
    }
    
    df = pd.DataFrame(data)
    excel_path = os.path.join(os.path.dirname(__file__), 'data', 'english.xlsx')
    df.to_excel(excel_path, index=False)
    print(f"Created English Excel file: {excel_path}")

def create_russian_excel():
    """Create Russian test Excel file"""
    data = {
        'Номер': [1, 2, 3, 4, 5],
        'Название': [
            'Введение в подкастинг', 
            'Оборудование для начинающих', 
            'Техники записи', 
            'Монтаж подкаста', 
            'Распространение и продвижение'
        ],
        'Описание': [
            'Узнайте основы подкастинга и что нужно для начала.',
            'Необходимое оборудование для начинающих подкастеров.',
            'Профессиональные техники записи для чистого звука.',
            'Советы и приемы для эффективного монтажа подкаста.',
            'Как распространять и продвигать подкаст для привлечения слушателей.'
        ],
        'Полное описание': [
            'В этом эпизоде мы рассмотрим основы подкастинга, включая планирование контента, выбор формата и понимание вашей аудитории.',
            'Мы обсудим микрофоны, наушники, аудиоинтерфейсы и программное обеспечение для записи, которые предлагают лучшее соотношение цены и качества для начинающих.',
            'Изучите профессиональные техники записи чистого звука, правильное размещение микрофона и работу с акустикой помещения.',
            'Этот эпизод охватывает рабочие процессы редактирования, рекомендации по программному обеспечению и техники, чтобы ваш подкаст звучал профессионально.',
            'Откройте для себя стратегии распространения подкаста на разных платформах и эффективного продвижения его в социальных сетях.'
        ],
        'Жанр': [
            'Образование,Технологии', 
            'Технологии,Оборудование', 
            'Образование,Аудио', 
            'Технологии,Производство', 
            'Маркетинг,Бизнес'
        ]
    }
    
    df = pd.DataFrame(data)
    excel_path = os.path.join(os.path.dirname(__file__), 'data', 'russian.xlsx')
    df.to_excel(excel_path, index=False)
    print(f"Created Russian Excel file: {excel_path}")

if __name__ == '__main__':
    # Create data directory if it doesn't exist
    data_dir = os.path.join(os.path.dirname(__file__), 'data')
    os.makedirs(data_dir, exist_ok=True)
    
    # Create Excel files
    create_english_excel()
    create_russian_excel()
    print("Done!")
