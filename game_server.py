#!/usr/bin/env python3

import subscription, util
from config import *

import sys, os, shutil
import logging
import requests
import json
from google.cloud import storage


def game_db_report(gametype, gameid, result):
    """Sends the result of the run to the database API endpoint"""
    try:
        response = requests.post(url=api_game_update(gametype, gameid), data={
            'status': result})
        response.raise_for_status()
    except:
        logging.critical('Could not report to database API endpoint')
        sys.exit(1)

def game_log_error(gametype, gameid, reason):
    """Reports a server-side error to the database and terminates with failure"""
    logging.error(reason)
    game_db_report(gametype, gameid, 'error')
    sys.exit(1)

def game_worker(gameinfo):
    """
    Runs a game as specified by the message
    Message contains JSON data, with 4 string parameters:
        gametype: string, either "scrimmage" or "tournament"
        gameid:   string, id of the game
        player1:  string, id of red player submission
        player2:  string, id of blue player submission
        maps:     string, comma separated list of maps
    Components delimited by semicolons
    """

    client = storage.Client()
    bucket = client.get_bucket(GCLOUD_BUCKET_SUBMISSION)

    try:
        gameinfo = json.loads(gameinfo)
        gametype = gameinfo['gametype']
        gameid   = gameinfo['gameid']
        player1  = gameinfo['player1']
        player2  = gameinfo['player2']
        maps     = gameinfo['maps']
    except:
        game_log_error(gametype, gameid, 'Game information in incorrect format')

    # Filesystem structure:
    # /tmp/bc20-game-{gameid}/
    #     `-- player1.zip
    #     `-- player2.zip
    #     `-- replay.bc20
    #     `-- classes
    #     |   `-- <robot 1>
    #         |   `-- RobotPlayer.class
    #         |   `-- other classes
    #     |   `-- <robot 2>
    #         |   `-- RobotPlayer.class
    #         |   `-- other classes    
    serverdir = os.path.dirname(os.path.realpath(__file__))
    rootdir = os.path.join('/', 'tmp', 'bc20-game-{}-{}'.format(gametype, gameid))
    classesdir = os.path.join(rootdir, 'classes')
    replaydestination = os.path.join(rootdir, 'replay.bc20')

    # Obtain player executables
    try:
        os.mkdir(rootdir)
        with open(os.path.join(rootdir, 'player1.zip'), 'wb') as file_obj:
            bucket.get_blob(os.path.join(player1, 'player.zip')).download_to_file(file_obj)
        with open(os.path.join(rootdir, 'player2.zip'), 'wb') as file_obj:
            bucket.get_blob(os.path.join(player2, 'player.zip')).download_to_file(file_obj)
    except:
        game_log_error(gametype, gameid, 'Could not retrieve executables from bucket')

    # Unzip player zips to classesdir
    os.mkdir(classesdir)
    result = util.monitor_command(
        ['unzip', 'player1.zip', '-d', classesdir],
        cwd=rootdir,
        timeout=TIMEOUT_UNZIP)
    if result[0] != 0:
        compile_log_error(submissionid, 'Could not decompress source file')
        os.mkdir(classesdir)
    result = util.monitor_command(
        ['unzip', 'player2.zip', '-d', classesdir],
        cwd=rootdir,
        timeout=TIMEOUT_UNZIP)
    if result[0] != 0:
        compile_log_error(submissionid, 'Could not decompress source file')

    # TODO: test command
    result = util.monitor_command(
        ['./gradlew', 'run',
            '-PteamA={}'.format(player1),
            '-PteamB={}'.format(player2),
            '-Pmaps={}'.format(maps),
            '-PclassLocation={}'.format(classesdir),
            '-Preplay={}'.format(replaydestination)
        ]
        cwd=serverdir,
        timeout=TIMEOUT_GAME)

    if result[0] != 0:
        game_log_error(gametype, gameid, 'Game execution had non-zero return code')

    bucket = client.get_bucket(GCLOUD_BUCKET_SUBMISSION)
    try:
        with open(replaydestination, 'rb') as file_obj:
            bucket.blob('{}-{}.bc20'.format(gametype, gameid)).upload_from_file(file_obj)
    except:
        game_log_error(gametype, gameid, 'Could not send replay file to bucket')

    # TODO: Interpret game result to send to database
    if True:
        game_db_report(gametype, gameid, 'redwon')
    else:
        game_db_report(gametype, gameid, 'bluewon')

    # Clean up working directory
    try:
        shutil.rmtree(rootdir)
    except:
        logging.warning('Could not clean up game execution directory')

if __name__ == '__main__':
    subscription.subscribe(GCLOUD_SUB_GAME_NAME, game_worker)
