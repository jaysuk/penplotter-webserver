import configparser

config = configparser.ConfigParser()

if config.read('config.ini'):
    print('Config file found. Reading config')
else:
    print("'config.ini' does not exist or could not be read. Creating a new one...")

    config['telegram'] = {
        'telegram_token': 'XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX',
        'telegram_chatid': 'XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX'
    }

    config['tasmota'] = {
        'tasmota_enable': 'false',
        'tasmota_ip': '192.168.1.101',
        'tasmota_on_delay': '2',
        'tasmota_off_delay': '30'
    }

    config['timelapse'] = {
        'timelapse_enable': 'false',
        'timelapse_auto_start': 'false',
        'timelapse_preview': 'false'
    }

    config['plotter'] = {
        'name': 'My Plotter',
        'port': '/dev/ttyAMA0',
        'device': 'hp7475a',
        'baudrate': '9600',
        'flowControl': 'CTS/RTS'
    }

    with open('config.ini', 'w') as configfile:
        config.write(configfile)
