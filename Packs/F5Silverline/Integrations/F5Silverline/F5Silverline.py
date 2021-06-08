import re

import demistomock as demisto
from CommonServerPython import *  # noqa # pylint: disable=unused-wildcard-import
from CommonServerUserPython import *  # noqa

import requests
import traceback
from typing import Dict, Any

requests.packages.urllib3.disable_warnings()  # pylint: disable=no-member

BASE_URL = "/api/v1/ip_lists"
TABLE_HEADERS_GET_OBJECTS = ['ID', 'IP', 'Expires At', 'List Target', 'Created At', 'Updated At']
PAGE_NUMBER_PATTERN = "(?<=page\[number]=).*?(?=&)"


class Client(BaseClient):
    def __init__(self, base_url: str, verify: bool, headers: dict, proxy: bool):
        """
        Client to use in the F5_Silverline integration. Overrides BaseClient.

        Args:
            base_url (str): URL to access when doing an http request.
            verify (bool): Whether to check for SSL certificate validity.
            headers (dict): Headers to set when when doing an http request.
            proxy (bool): Whether the client should use proxies.
        """
        super().__init__(base_url=base_url, verify=verify, proxy=proxy)
        self._headers = headers

    def request_ip_objects(self, body: dict, method: str, url_suffix: str, params: dict, resp_type='json') -> Dict:
        """
        Makes an HTTP request to F5 Silverline API by the given arguments.
        Args:
            body (dict): The dictionary to send in a 'POST' request.
            method (str): HTTP request method (GET/POST/DELETE).
            url_suffix (str): The API endpoint.
            params (dict): URL parameters to specify the query.
            resp_type (str): Determines which data format to return from the HTTP request. The default is 'json'.
        """
        demisto.debug(f'current request is: method={method}, body={body}, url suffix={url_suffix},'
                      f'params={params}, resp_type={resp_type}')

        return self._http_request(method=method, json_data=body, url_suffix=url_suffix, params=params,
                                  headers=self._headers, resp_type=resp_type)


def test_module(client: Client) -> str:
    """
    Tests API connectivity and authentication. Does a GET request for this purpose.
    """
    try:
        client.request_ip_objects(body={}, method='GET', url_suffix='denylist/ip_objects', params={})
        message = 'ok'
    except DemistoException as e:
        if 'Unauthorized' in str(e):
            message = 'Authorization Error: make sure API Key is correctly set'
        else:
            raise e
    return message


def paging_args_to_params(page_size, page_number):
    """
    Returns the parameters to the HTTP request when using paging.
    """
    try:
        page_size = int(page_size)
        page_number = int(page_number)
    except ValueError:
        raise ValueError("page_number and page_size should be numbers")

    params = {'page[size]': page_size, 'page[number]': page_number}
    return params


def add_ip_objects_command(client: Client, args: Dict[str, Any]) -> CommandResults:
    """
    Adds a new IP object to the requested list type (denylist or allowlist).
    IP object includes an IP address (mandatory). Other fields are optional and have default values.
    Note: Human readable appears only if the HTTP request did not fail.
    API docs: https://portal.f5silverline.com/docs/api/v1/ip_objects.md (POST section)
    """
    list_type = args['list_type']
    ip_address = args['IP']
    list_target = args.get('list_target', 'proxy-routed')
    mask = args.get('mask', '32')
    duration = int(args.get('duration', 0))
    note = args.get('note', "")
    tags = argToList(args.get('tags', []))
    url_suffix = f'{list_type}/ip_objects'

    body = define_body_for_add_ip_command(list_target, mask, ip_address, duration, note, tags)

    client.request_ip_objects(body=body, method='POST', url_suffix=url_suffix, params={}, resp_type='content')
    human_readable = f"IP object with IP address: {ip_address} added successfully into the {list_type} list."
    return CommandResults(readable_output=human_readable)


def define_body_for_add_ip_command(list_target, mask, ip_address, duration, note, tags):
    """
    API docs: https://portal.f5silverline.com/docs/api/v1/ip_objects.md (POST section)
    prepares the body of a POST request in order to add an IP
    """
    return \
        {
            "list_target": list_target,
            "data":
                {
                    "id": "",
                    "type": "ip_objects",
                    "attributes": {
                        "mask": mask,
                        "ip": ip_address,
                        "duration": duration
                    },
                    "meta": {
                        "note": note,
                        "tags": tags
                    }
                }
        }


def delete_ip_objects_command(client: Client, args: Dict[str, Any]) -> CommandResults:
    """
    Deletes an exist IP object from the requested list type (denylist or allowlist) by its object id (mandatory).
    Note: Human readable appears only if the HTTP request did not fail.
    API docs: https://portal.f5silverline.com/docs/api/v1/ip_objects.md (DELETE section)
    """
    list_type = args['list_type']
    object_id = args['object_id']
    url_suffix = f'{list_type}/ip_objects/{object_id}'

    client.request_ip_objects(body={}, method='DELETE', url_suffix=url_suffix, params={}, resp_type='content')
    human_readable = f"IP object with ID: {object_id} deleted successfully from the {list_type} list."
    return CommandResults(readable_output=human_readable)


def handle_paging(page_number, page_size):
    """
    * Returns whether the user wants to get the results by paging (page size and page number).
    * Returns the parameters dict to the HTTP request when using paging, empty dict will be returned if paging was
    not required.
    """
    is_paging_required = False
    params = {}
    if page_number and page_size:
        params = paging_args_to_params(page_size, page_number)
        is_paging_required = True
    return is_paging_required, params


def add_paging_to_outputs(paging_dict, page_number):
    link_to_current_obj = paging_dict.get('self')
    link_to_last_obj = paging_dict.get('last')
    current_page_number = re.search(PAGE_NUMBER_PATTERN, link_to_current_obj).group(
        0) if link_to_current_obj else page_number
    last_page_number = re.search(PAGE_NUMBER_PATTERN, link_to_last_obj).group(
        0) if link_to_last_obj else current_page_number  # if the current page is also the last one
    return current_page_number, last_page_number


def paging_outputs_dict(current_page_number, last_page_number, page_size):
    return {
        'current_page_number': current_page_number,
        'current_page_size': page_size,
        'last_page_number': last_page_number
    }


def paging_data_to_human_readable(current_page_number, last_page_number, page_size):
    output = f"Current page number: {current_page_number}\n "
    if not last_page_number:
        # if the current page number is also the last page number
        last_page_number = current_page_number
    output += f"Last page number: {last_page_number}\n"
    if page_size:
        output += f"Current page size: {page_size}"
    return output


def get_ip_objects_list_command(client: Client, args: Dict[str, Any]) -> CommandResults:
    """
    Gets a list of IP objects by the requested list type (denylist or allowlist).
    If the object_id argument is given, only those IP objects will be displayed. Otherwise, all IP objects that match
    the given list_type will be displayed.
    API docs: https://portal.f5silverline.com/docs/api/v1/ip_objects.md (GET section)
    """
    list_type = args['list_type']
    object_ids = argToList(args.get('object_id'))
    page_number = args.get('page_number')
    page_size = args.get('page_size')
    url_suffix = f'{list_type}/ip_objects'
    paging_data = []
    paging_data_human_readable = ""
    is_paging_required, params = handle_paging(page_number, page_size)

    if not object_ids:
        # in case the user wants to get all the IP objects and not specific ones
        response = client.request_ip_objects(body={}, method='GET', url_suffix=url_suffix, params=params)
        outputs = response.get('data')
        human_results = parse_get_ip_object_list_results(response)
        if is_paging_required and outputs:
            current_page_number, last_page_number = add_paging_to_outputs(response.get('links'), page_number)
            paging_data = paging_outputs_dict(current_page_number, last_page_number, page_size)
            paging_data_human_readable = paging_data_to_human_readable(current_page_number, last_page_number, page_size)
    else:
        human_results, outputs = get_ip_objects_by_ids(client, object_ids, list_type, params)  # type: ignore

    human_readable = tableToMarkdown('F5 Silverline IP Objects', human_results, TABLE_HEADERS_GET_OBJECTS,
                                     removeNull=True)
    human_readable += paging_data_human_readable

    if not human_results and is_paging_required:
        human_readable = "No results were found. Please try to run the command without page_number and page_size to " \
                         "get all existing IP objects."

    return CommandResults(
        readable_output=human_readable,
        outputs_prefix='F5Silverline',
        outputs_key_field='id',
        outputs={"IPObjectList": outputs, "Paging": paging_data}
    )


def get_ip_objects_by_ids(client: Client, object_ids: list, list_type: str, params: dict):
    """
    In case the user requests one or more specific IP objects (by their object_id). For each id we make a separate
    HTTP request (the API does not support list of ids).
    """
    human_results = []
    outputs = []
    for object_id in object_ids:
        url_suffix = f'{list_type}/ip_objects/{object_id}'
        res = client.request_ip_objects(body={}, method='GET', url_suffix=url_suffix, params=params)
        outputs.append(res.get('data'))
        human_results.append(parse_get_ip_object_list_results(res)[0])
    return human_results, outputs


def parse_get_ip_object_list_results(results: Dict):
    """
    Parsing the API response after requesting the IP object list. Parsing maps the important fields that will appear
    as the human readable output.
    An example for a response: https://portal.f5silverline.com/docs/api/v1/ip_objects.md.
    Under the title "Success Response -> Body"
    """
    parsed_results = []
    results_data = results.get('data')  # type: ignore
    demisto.debug(f"response is {results_data}")
    if isinstance(results_data, dict):
        # in case the response consist only single ip object, the result is a dict and not a list, but we want to handle
        # those cases in the same way
        results_data = [results_data]
    for ip_object in results_data:  # type: ignore
        if ip_object:
            parsed_results.append({
                'ID': ip_object.get('id'),
                'IP': ip_object.get('attributes').get('ip'),
                'Expires At': ip_object.get('attributes').get('expires_at'),
                'List Target': ip_object.get('attributes').get('list_target'),
                'Created At': ip_object.get('meta').get('created_at'),
                'Updated At': ip_object.get('meta').get('updated_at')
            })
    return parsed_results


def main() -> None:
    params = demisto.params()
    access_token = params.get('token').get('password')
    base_url = urljoin(params.get('url'), BASE_URL)
    verify_certificate = not params.get('insecure', False)
    proxy = params.get('proxy', False)
    demisto.debug(f'Command being called is {demisto.command()}')
    try:
        headers: Dict = {"X-Authorization-Token": access_token, "Content-Type": 'application/json'}

        client = Client(
            base_url=base_url,
            verify=verify_certificate,
            headers=headers,
            proxy=proxy)

        args = demisto.args()

        if demisto.command() == 'test-module':
            return_results(test_module(client))

        elif demisto.command() == 'f5-silverline-ip-objects-list':
            return_results(get_ip_objects_list_command(client, args))

        elif demisto.command() == 'f5-silverline-ip-object-add':
            return_results(add_ip_objects_command(client, args))

        elif demisto.command() == 'f5-silverline-ip-object-delete':
            return_results(delete_ip_objects_command(client, args))
        else:
            raise NotImplementedError(f'{demisto.command()} is not an existing F5 Silverline command')

    except Exception as e:
        demisto.error(traceback.format_exc())
        return_error(f'Failed to execute {demisto.command()} command.\nError:\n{str(e)}')


if __name__ in ('__main__', '__builtin__', 'builtins'):
    main()
