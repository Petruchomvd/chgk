from modules_pars.db_utils import innit_db, insert_game
from modules_pars.utils import save_game, checke_if_saved
from game_parse import get_last_game, game_parsing
from modules_pars.selenium_setup import init_selenium_driver

FALLBACK_SCAN_LIMIT = 20000
MAX_CONSECUTIVE_MISSES = 200


def main():
    connection, cursor = innit_db()
    driver = init_selenium_driver()

    try:
        last_game = get_last_game(driver)
        if last_game is not None:
            game_id_iterable = range(1, last_game + 1)
            print(f'Parsed index metadata -> scanning ids 1..{last_game}.')
        else:
            print('Falling back to sequential scan with gap threshold.')
            game_id_iterable = range(1, FALLBACK_SCAN_LIMIT + 1)

        consecutive_misses = 0

        for game_number in game_id_iterable:
            if checke_if_saved(game_number):
                # Already seen this id earlier.
                continue

            print(f'Processing game #{game_number}...')
            game_data = game_parsing(game_number, driver, connection, cursor)

            if not game_data:
                if last_game is None:
                    consecutive_misses += 1
                    if consecutive_misses >= MAX_CONSECUTIVE_MISSES:
                        first_missing = game_number - MAX_CONSECUTIVE_MISSES + 1
                        print(f'Stopping after {MAX_CONSECUTIVE_MISSES} consecutive missing ids (from #{first_missing}).')
                        break
                continue

            consecutive_misses = 0
            if insert_game(connection, cursor, game_data):
                save_game(game_number)
    finally:
        connection.commit()
        connection.close()
        driver.quit()


if __name__ == '__main__':
    main()
