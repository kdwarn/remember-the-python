'''
    TO DO:
        - get 'due' and other tasks attributes properly. Might have to do another
          loop through the tasks within taskseries
        - for export(), --completed should include # days to go back for tasks
        - I'm not sure if I have to do the full authentication thing if a token
          expires or if there's a simple token refresh process
'''

import datetime
import click
import requests
import hashlib
import pickle
from icecream import ic
from config import API_KEY, SHARED_SECRET


auth_url = 'https://www.rememberthemilk.com/services/auth/'
methods_url = 'https://api.rememberthemilk.com/services/rest/'

user_settings = 'user.pickle'

# load settings from user_settings, create both file and data object if necessary
try:
    with open(user_settings, 'rb') as f:
        settings = pickle.load(f)
except FileNotFoundError:
    with open(user_settings, 'wb') as f:
        settings = {}
except EOFError:
    settings = {}


class NoTasksException(Exception):
    pass
    # click.secho("No tasks found with given parameters.", fg='red')

################################################################################
# CLASSES
################################################################################
class Task:

    def __init__(self, taskseries):
        self.id = taskseries['id']
        self.name = taskseries['name']

        if 'due' in taskseries['task']:
            self.due = taskseries['task']['due']
        else:
            self.due = ''

################################################################################
#  HELPER FUNCTIONS
################################################################################
def save(settings):
    ''' Save user settings to file. '''

    with open(user_settings, 'wb') as f:
        pickle.dump(settings, f)


def get_data_or_raise_exception(response):
    data = response.json()['rsp']

    if data['stat'] != 'ok':
        click.echo("There's been an error.")
        raise Exception('{}: {}'.format({data['err']['code']}, {data['err']['msg']}))

    return data

def make_api_sig(params):
    '''
    Creates an API signature as required by RTM. See
    https://www.rememberthemilk.com/services/api/authentication.rtm

    This creates a string consisting of the SHARED SECRET provided by RTM concatenated
    with the sorted key/value pairs of the parameters to be sent. This api_sig
    then becomes another parameter sent with the request to RTM.
    '''

    api_sig = SHARED_SECRET + ''.join('{}{}'.format(key, value) for key, value in sorted(params.items()))
    api_sig = hashlib.md5(api_sig.encode('utf-8'))
    return api_sig.hexdigest()


def get_frob():
    ''' Returns *frob*. Part of the authentication process.'''

    params = {'method':'rtm.auth.getFrob',
              'api_key':API_KEY,
              'format':'json'}
    # create signature from existing params and add as parameter
    params['api_sig'] = make_api_sig(params)

    r = requests.get(methods_url, params=params)

    if r.status_code != 200:
        raise Exception("Error ({}) connecting to Remember the Milk. Please "
                        "try again later.".format(r.status_code))

    data = get_data_or_raise_exception(r)

    return data['frob']


def authenticate():
    '''
    Authenticates user to RTM and stores token to *user_settings* file.

    See https://www.rememberthemilk.com/services/api/authentication.rtm.
    '''

    frob = get_frob()
    params = {'api_key':API_KEY,
              'frob':frob,
              'perms':'read'}
    params['api_sig'] = make_api_sig(params)

    r = requests.get(auth_url, params=params)

    if r.status_code != 200:
        raise Exception("Error ({}) connecting to Remember the Milk. Please "
                        "try again later.".format(r.status_code))

    click.echo('')
    click.echo('Open the following link in your browser in order to approve authentication '
        'from Remember the Milk:')
    click.echo('')
    click.echo(r.url)

    # pause the application while the user approves authentication
    value = click.prompt('Type "c" and then press enter to continue.')

    # now get the authentication token
    params = {'api_key':API_KEY,
              'method':'rtm.auth.getToken',
              'format':'json',
              'frob':frob}

    # sign every request
    params['api_sig'] = make_api_sig(params)

    r = requests.get(methods_url, params=params)

    r = r.json()['rsp']
    #r_token.json()
    settings['token'] = r['auth']['token']
    settings['username'] = r['auth']['user']['username']
    settings['name'] = r['auth']['user']['fullname']
    save(settings)

    click.echo('Congrats, {}, it worked!'.format(settings['name']))

    return


def check_token():
    ''' Check user token. Do nothing, refresh token, or authenticate. '''

    if 'token' in settings:
        params = {'api_key':API_KEY,
                  'method': 'rtm.auth.checkToken',
                  'format':'json',
                  'auth_token':settings['token']}
        params['api_sig'] = make_api_sig(params)

        r = requests.get(methods_url, params=params)

        if r.status_code != 200:
            raise Exception("Error ({}) connecting to Remember the Milk. Please "
                            "try again later.".format(r.status_code))

        r = r.json()['rsp']

        if r['stat'] != 'ok':
            click.echo("There's been an error.")
            click.echo('{}: {}'.format({r['err']['code']}, {r['err']['msg']}))
            click.echo('Attempting to reauthenticate...')
            authenticate()

    else:
        authenticate()

    return


def get_lists():
    """ Get all of the user's lists."""

    check_token()
    params = {'api_key':API_KEY,
              'method':'rtm.lists.getList',
              'format':'json',
              'auth_token':settings['token']}

    params['api_sig'] = make_api_sig(params)

    r = requests.get(methods_url, params=params)
    return get_data_or_raise_exception(r)['lists']['list']


def get_tasks(list_name, completed, incomplete):
    ''' Get user tasks, in list_name if given.

    Inputs:
        -list_name: name of list_name
        -completed: integer of number of days in past to get tasks from
        -incomplete: boolean
    '''

    check_token()
    params = {'api_key':API_KEY,
              'method':'rtm.tasks.getList',
              'format':'json',
              'auth_token':settings['token']}

    if list_name:
        lists = get_lists()

        for rtm_list in lists:
            if list_name == rtm_list['name']:
                list_id = rtm_list['id']

        if not list_id:
            return "Sorry, list not found."

        params['list_id'] = list_id

    if completed:
        # figure out x days ago date
        since = datetime.date.today() - datetime.timedelta(days=completed)
        params['filter'] = 'completedAfter:' + str(since)

    if incomplete:
        params['filter'] = 'status:incompleted'

    params['api_sig'] = make_api_sig(params)

    r = requests.get(methods_url, params=params)
    rtm_tasks = get_data_or_raise_exception(r)

    tasks = []

    if 'list' in rtm_tasks['tasks']:
        # return list of Task objects
        for rtm_list in rtm_tasks['tasks']['list']:
            for taskseries in rtm_list['taskseries']:
                tasks.append(Task(taskseries))
    else:
        raise NoTasksException

    return tasks


################################################################################
# commands
################################################################################
@click.group()
def main():
    """Command line interface for Remember the Milk.

    Type "<command> --help" to see options and additional info."""


@main.command()
def test():
    check_token()
    click.echo('Hello {}!'.format(settings['name']))


@main.command()
@click.option('--archived', is_flag=True, help="Show archived lists.")
@click.option('--smart', is_flag=True, help="Show smart lists.")
@click.option('--all', is_flag=True, help="Show all lists.")
def lists(archived, smart, all):
    '''List your lists!'''

    lists = get_lists()

    sub_list = []

    for rtm_list in lists:
        if all:
            sub_list.append(rtm_list)
        else:
            if archived and smart:
                if rtm_list['smart'] == '1' and rtm_list['archived'] == '1':
                    sub_list.append(rtm_list)
            elif archived and not smart:
                if rtm_list['smart'] == '0' and rtm_list['archived'] == '1':
                    sub_list.append(rtm_list)
            elif not archived and smart:
                if rtm_list['smart'] == '1' and rtm_list['archived'] == '0':
                    sub_list.append(rtm_list)
            else:
                if rtm_list['smart'] == '0' and rtm_list['archived'] == '0':
                    sub_list.append(rtm_list)

    if not sub_list:
        click.secho('No lists to show.', fg='red')
    else:
        for rtm_list in sorted(sub_list, key=lambda k: k['name'].lower()):
            click.echo(rtm_list['name'])
    return


@main.command()
@click.option('--list_name', '-l', default='', help="List name.")
@click.option('--completed', '-c', default=0, help="Completed tasks only. Defaults to tasks completed in the last 30 days.")
@click.option('--incomplete', '-i', is_flag=True, help="Incomplete tasks only.")
def tasks(list_name, completed, incomplete):
    """List tasks by various attributes."""
    try:
        tasks = get_tasks(list_name, completed, incomplete)
    except NoTasksException:
        click.secho("No tasks found with those parameters.", fg='red')
        return

    for task in tasks:
        print(task.name, '(due:', task.due, ')')

    return

@main.command()
@click.option('--list_name', '-l', default='', help="List name.")
@click.option('--completed', '-c', default='', help="Completed tasks only. Defaults to tasks completed in the last 30 days.")
@click.option('--incomplete', '-i', is_flag=True, help="Incomplete tasks only.")
def export(list_name, completed, incomplete):
    """Export tasks to pdf."""

    tasks = get_tasks(list_name, completed, incomplete)

    # for task in tasks:


if __name__ == "__main__":
    main()
