import logging
import os
import random
import sys
import time

import requests
import vk_api
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)


PRACTICUM_TOKEN = os.getenv('PRACTICUM_TOKEN')
VK_TOKEN = os.getenv('VK_TOKEN')
VK_USER_ID = os.getenv('VK_USER_ID')
RETRY_PERIOD = 600
ENDPOINT = 'https://practicum.yandex.ru/api/user_api/homework_statuses/'
HEADERS = {'Authorization': f'OAuth {PRACTICUM_TOKEN}'}
HOMEWORK_VERDICTS = {
    'approved': 'Работа проверена: ревьюеру всё понравилось. Ура!',
    'reviewing': 'Работа взята на проверку ревьюером.',
    'rejected': 'Работа проверена: у ревьюера есть замечания.'
}


def check_tokens():
    """Проверить обязательные переменные окружения перед запуском.

    Вернуть True, если все переменные заданы. При отсутствии хотя бы
    одной записать CRITICAL и завершить программу с кодом 1.
    """
    required_tokens = {
        'PRACTICUM_TOKEN': PRACTICUM_TOKEN,
        'VK_TOKEN': VK_TOKEN,
        'VK_USER_ID': VK_USER_ID,
    }
    missing = [name for name, value in required_tokens.items() if not value]
    if missing:
        logging.critical(
            'Отсутствуют обязательные переменные: %s', ', '.join(missing)
        )
        sys.exit(1)
    logging.info('Все обязательные переменные найдены.')
    return True


def send_message(vk, message):
    """Отправить message пользователю VK через переданный клиент vk.

    Вернуть True при успехе и записать DEBUG. При ошибке записать ERROR
    и вернуть False, чтобы уведомление можно было отправить повторно.
    """
    try:
        vk.messages.send(
            user_id=VK_USER_ID,
            message=message,
            random_id=random.randint(0, 100000)
        )
    except Exception as error:
        logging.error(
            'Ошибка отправки сообщения в VK: %s', type(error).__name__
        )
        return False
    logging.debug('Сообщение успешно отправлено в VK.')
    return True


def get_api_answer(timestamp):
    """Получить ответ API со статусами начиная с timestamp в Unix time.

    Передать OAuth-токен и параметр from_date, вернуть разобранный JSON.
    При сетевой ошибке или HTTP-коде, отличном от 200, вызвать RuntimeError;
    при некорректном JSON вызвать ValueError.
    """
    params = {'from_date': int(timestamp)}
    try:
        response = requests.get(
            url=ENDPOINT, headers=HEADERS, params=params, timeout=5
        )
    except requests.exceptions.RequestException as error:
        raise RuntimeError(
            f'Ошибка запроса к API Практикума: {type(error).__name__}'
        ) from error
    if response.status_code != 200:
        raise RuntimeError(
            f'API Практикума вернул HTTP {response.status_code}.'
        )
    try:
        return response.json()
    except ValueError as error:
        raise ValueError('Ответ API не является валидным JSON.') from error


def check_response(response):
    """Проверить структуру ответа API и вернуть список домашних работ.

    Проверить ключи homeworks и current_date и типы их значений.
    При отсутствии ключа вызвать KeyError, при неверном типе — TypeError.
    Для пустого списка работ записать DEBUG.
    """
    if not isinstance(response, dict):
        raise TypeError('Ответ API должен быть словарём.')
    for key in ('homeworks', 'current_date'):
        if key not in response:
            raise KeyError(f'В ответе API отсутствует ключ {key}.')
    homeworks = response['homeworks']
    if not isinstance(homeworks, list):
        raise TypeError('Значение homeworks должно быть списком.')
    if type(response['current_date']) is not int:
        raise TypeError('Значение current_date должно быть Unix time.')
    if not homeworks:
        logging.debug("Новых работ не найдено в ответе API.")
    return homeworks


def parse_status(homework):
    """Составить уведомление из элемента списка домашних работ.

    Вернуть строку с названием работы, комментарием и вердиктом.
    При неверном типе вызвать TypeError, при отсутствии обязательного
    ключа — KeyError, при неизвестном статусе — ValueError.
    """
    if not isinstance(homework, dict):
        raise TypeError('Домашняя работа должна быть словарём.')
    for key in ('homework_name', 'status'):
        if key not in homework:
            raise KeyError(f'В домашней работе отсутствует ключ {key}.')
    name = homework['homework_name']
    status = homework['status']
    if status not in HOMEWORK_VERDICTS:
        raise ValueError(f'Неизвестный статус домашней работы: {status}.')
    comment = homework.get('reviewer_comment', '')
    message = (
        f'Изменился статус проверки работы "{name}".\n'
    )
    if comment:
        clean_comment = comment.replace('\n', ' ')
        message += f'Комментарий ревьюера: {clean_comment}\n'
    return message + HOMEWORK_VERDICTS[status]


def main():
    """Запустить опрос API каждые RETRY_PERIOD секунд и уведомления в VK.

    Проверить окружение и создать VK-клиент. Обновлять время выборки после
    успешной отправки всех статусов. Логировать каждую ошибку, отправляя
    уведомление о ней один раз до следующего успешного цикла.
    """
    check_tokens()
    vk_session = vk_api.VkApi(token=VK_TOKEN)
    vk = vk_session.get_api()
    last_timestamp = int(time.time()) - 3600
    logging.info("Бот запущен и начал мониторинг статусов...")
    reported_errors = set()
    sent_messages = set()

    while True:
        try:
            response = get_api_answer(last_timestamp)
            homeworks = check_response(response)
            for homework in homeworks:
                message = parse_status(homework)
                if message not in sent_messages:
                    if send_message(vk, message) is False:
                        break
                    sent_messages.add(message)
            else:
                last_timestamp = response['current_date']
                sent_messages.clear()
                reported_errors.clear()
        except Exception as error:
            message = f'Ошибка работы бота: {error}'
            logging.error(message)
            if message not in reported_errors:
                if send_message(vk, message) is not False:
                    reported_errors.add(message)
        time.sleep(RETRY_PERIOD)


if __name__ == '__main__':
    main()
