import logging
import os
import random
import sys
import time
from http import HTTPStatus

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

    Вернуть True, если все переменные заданы, иначе False.
    """
    required_tokens = {
        'PRACTICUM_TOKEN': PRACTICUM_TOKEN,
        'VK_TOKEN': VK_TOKEN,
        'VK_USER_ID': VK_USER_ID,
    }
    missing = [name for name, value in required_tokens.items() if not value]
    return not missing


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
    При сетевой ошибке вызвать RuntimeError. При HTTP-коде, отличном от
    HTTPStatus.OK, или некорректном JSON вызвать ValueError.
    """
    params = {'from_date': int(timestamp)}
    logging.debug(
        'Начало запроса к API: адрес=%s, параметры=%s', ENDPOINT, params
    )
    try:
        response = requests.get(
            url=ENDPOINT, headers=HEADERS, params=params, timeout=5
        )
    except requests.exceptions.RequestException as error:
        raise RuntimeError(
            f'Ошибка запроса к API Практикума: {type(error).__name__}'
        ) from error
    if response.status_code != HTTPStatus.OK:
        raise ValueError(
            f'API Практикума вернул HTTP {response.status_code}.'
        )
    data = response.json()
    logging.debug(
        'Запрос к API успешно завершён: адрес=%s, параметры=%s',
        ENDPOINT, params
    )
    return data


def check_response(response):
    """Проверить структуру ответа API и вернуть список домашних работ.

    Проверить наличие ключа homeworks и тип его значения.
    При отсутствии ключа вызвать KeyError, при неверном типе — TypeError.
    """
    logging.debug('Начало проверки ответа сервера.')
    if not isinstance(response, dict):
        raise TypeError(
            f'Ответ API должен быть словарём, получен тип {type(response)}.'
        )
    if 'homeworks' not in response:
        raise KeyError('В ответе API отсутствует ключ homeworks.')
    homeworks = response['homeworks']
    if not isinstance(homeworks, list):
        raise TypeError(
            'Значение homeworks должно быть списком, '
            f'получен тип {type(homeworks)}.'
        )
    logging.debug('Проверка ответа сервера успешно завершена.')
    return homeworks


def parse_status(homework):
    """Составить уведомление из элемента списка домашних работ.

    Вернуть строку с названием работы, комментарием и вердиктом.
    При неверном типе вызвать TypeError, при отсутствии обязательного
    ключа — KeyError, при неизвестном статусе — ValueError.
    """
    logging.debug('Начало проверки статуса домашней работы.')
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
    message += HOMEWORK_VERDICTS[status]
    logging.debug('Проверка статуса домашней работы успешно завершена.')
    return message


def main():
    """Запустить опрос API каждые RETRY_PERIOD секунд и уведомления в VK.

    Проверить окружение и создать VK-клиент. Обрабатывать первую работу
    и обновлять время выборки после успешного цикла. Логировать каждую
    ошибку и сравнивать её сообщение с последним отправленным, чтобы
    не повторять уведомления.
    """
    if not check_tokens():
        logging.critical(
            'Отсутствует хотя бы одна обязательная переменная окружения.'
        )
        sys.exit(1)
    logging.info('Все обязательные переменные найдены.')
    vk_session = vk_api.VkApi(token=VK_TOKEN)
    vk = vk_session.get_api()
    last_timestamp = int(time.time()) - 3600
    logging.info("Бот запущен и начал мониторинг статусов...")
    last_error_message = ''

    while True:
        try:
            response = get_api_answer(last_timestamp)
            homeworks = check_response(response)
            notification_sent = True
            if not homeworks:
                logging.debug('Статус домашней работы не изменился.')
            else:
                homework = homeworks[0]
                message = parse_status(homework)
                notification_sent = send_message(vk, message) is not False
            if notification_sent:
                last_timestamp = (
                    response.get('current_date') or int(time.time())
                )
                last_error_message = ''
        except Exception as error:
            message = f'Ошибка работы бота: {error}'
            logging.error(message)
            if (message != last_error_message
                    and send_message(vk, message) is not False):
                last_error_message = message
        finally:
            time.sleep(RETRY_PERIOD)


if __name__ == '__main__':
    main()
