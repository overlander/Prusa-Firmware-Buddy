import aiohttp
import asyncio
import logging
import pytest

from .actions import encoder, screen, temperature, network
from simulator import MachineType, Thermistor, Printer

pytestmark = pytest.mark.asyncio


def wui_base_url(printer):
    return f'http://localhost:{network.proxy_http_port_get(printer)}'


@pytest.fixture
async def wui_client(printer):
    # Make sure the printer is running before returning the client.
    await screen.wait_for_text(printer, 'HOME')

    async with aiohttp.ClientSession(
            base_url=wui_base_url(printer)) as session:
        yield session


def valid_headers():
    return {'X-Api-Key': '0123456789'}


async def test_web_interface_is_accessible(wui_client: aiohttp.ClientSession):
    response = await wui_client.get('/')
    assert response.ok
    # Check we can actually download the whole page and that it looks htmley a
    # bit.
    body = await response.text()
    assert '<html>' in body


async def test_not_found(wui_client: aiohttp.ClientSession):
    for non_existent in ['/nonsense', '/api/not']:
        response = await wui_client.get(non_existent)
        assert response.status == 404


async def test_auth(wui_client: aiohttp.ClientSession):
    # Not getting in when no X-Api-Kep is present.
    all_endpoints = ['version', 'printer', 'job', 'files']
    for endpoint in all_endpoints:
        response = await wui_client.get('/api/' + endpoint)
        assert response.status == 401

    # Not getting in when the api key is wrong
    # We try some that are _close_ too.
    for wrong_pass in ["123456789", "012345678", "0123456789abc"]:
        headers = {"X-Api-Key": wrong_pass}
        for endpoint in all_endpoints:
            response = await wui_client.get('/api/' + endpoint,
                                            headers=headers)
            assert response.status == 401

    # And correct password lets one come in and returns a json
    for endpoint in all_endpoints:
        response = await wui_client.get('/api/' + endpoint,
                                        headers=valid_headers())
        assert response.ok
        assert response.headers["CONTENT-TYPE"] == 'application/json'
        await response.json()  # Tries to parse and throws if fails decoding


async def test_idle_version(wui_client: aiohttp.ClientSession):
    version_r = await wui_client.get('/api/version', headers=valid_headers())
    version = await version_r.json()
    for exp in ["text", "hostname", "api", "server"]:
        assert exp in version


async def test_idle_printer_api(wui_client: aiohttp.ClientSession):
    printer_r = await wui_client.get('/api/printer', headers=valid_headers())
    printer_j = await printer_r.json()
    tele = printer_j["telemetry"]
    assert tele["material"] == "---"
    assert tele["print-speed"] == 100
    temp = printer_j["temperature"]
    assert temp["tool0"]["target"] == 0
    assert temp["bed"]["target"] == 0
    assert printer_j["state"]["text"] == "Operational"
    assert not printer_j["state"]["flags"]["printing"]


async def test_idle_job(wui_client: aiohttp.ClientSession):
    job_r = await wui_client.get('/api/job', headers=valid_headers())
    job = await job_r.json()
    assert job["state"] == "Operational"
    assert job["job"] is None
    assert job["progress"] is None


@pytest.fixture
async def running_printer_client(printer_factory, printer_flash_dir, data_dir):
    gcode_name = 'box.gcode'
    gcode = (data_dir / gcode_name).read_bytes()
    (printer_flash_dir / gcode_name).write(gcode)

    async with printer_factory() as printer:
        printer: Printer
        # Wait for boot
        await screen.wait_for_text(printer, 'HOME')
        # Wait for mounting of the USB
        await screen.wait_for_text(printer, 'Print')

        # "Preheat" the printer
        await temperature.set(printer, Thermistor.BED, 60)
        await temperature.set(printer, Thermistor.NOZZLE, 170)

        # Start a print
        await encoder.click(printer)
        # For some reason, box.gcode is not discovered, the OCR places a space
        # in there.
        await screen.wait_for_text(printer, 'gcode')
        await encoder.click(printer)
        await screen.wait_for_text(printer, 'Print')
        await encoder.click(printer)
        await screen.wait_for_text(printer, 'Tune')

        client = aiohttp.ClientSession(base_url=wui_base_url(printer))

        # Wait for the printer to go into the printing state. This might take a
        # little bit of time, so try few times before giving up
        for attempt in range(1, 10):
            printer_r = await client.get('/api/printer',
                                         headers=valid_headers())
            printer_j = await printer_r.json()
            if printer_j["temperature"]["bed"]["target"] > 0 and printer_j[
                    "state"]["flags"]["printing"]:
                break
            logging.info("Didn't start print yet? " + str(printer_j))
            await asyncio.sleep(0.3)
        else:
            assert False  # Didn't reach the part where there's a temperature

        yield client


async def test_printing_telemetry(running_printer_client):
    """
    Ask for telemetry information during a print and get something useful.
    """
    printer_r = await running_printer_client.get('/api/printer',
                                                 headers=valid_headers())
    printer_j = await printer_r.json()
    assert printer_j["temperature"]["bed"]["target"] == 60
    assert printer_j["temperature"]["tool0"]["target"] == 170
    assert printer_j["temperature"]["tool0"]["display"] == 170
    assert printer_j["telemetry"]["temp-bed"] > 40
    assert printer_j["telemetry"]["temp-bed"] == printer_j["temperature"][
        "bed"]["actual"]


async def test_printing_job(running_printer_client):
    job_r = await running_printer_client.get('/api/job',
                                             headers=valid_headers())
    job = await job_r.json()
    logging.info("Received job info " + str(job))
    assert job["state"] == "Printing"
    # Hopefully the test won't take that long
    assert job["progress"]["printTime"] < 300
    # FIXME: Fails. #BFW-2332
    # assert job["job"]["file"]["name"] == gcode_name
    # It's something around 25 minutes...
    # FIXME: The following sometimes fail too. It seems marlin_vars are not
    # always properly updated?
    assert job["progress"]["printTimeLeft"] > 1000
    assert job["job"]["estimatedPrintTime"] > 1000
    assert job["job"]["estimatedPrintTime"] == job["progress"][
        "printTime"] + job["progress"]["printTimeLeft"]


# See below, needs investigation
@pytest.mark.skip()
async def test_upload(wui_client: aiohttp.ClientSession):
    data = aiohttp.FormData()
    data.add_field('file', b'', filename='empty.gcode')
    # TODO: Why does this send the "printer" to bluescreen? Doesn't happen on a
    # real one.
    response = await wui_client.post('/api/files/sdcard',
                                     data=data,
                                     headers=valid_headers())
    assert response.ok
    assert response.headers["CONTENT-TYPE"] == 'application/json'
    await response.json()  # Tries to parse and throws if fails decoding
    # See if we get the one-click-print screen with the just-uploaded file
    await screen.wait_for_text(printer, 'empty.gcode')
    # TODO: Turn off printer and see that the file appeared on the flash drive.


async def test_upload_notauth(wui_client: aiohttp.ClientSession):
    data = aiohttp.FormData()
    data.add_field('file', b'', filename='empty.gcode')
    response = await wui_client.post('/api/files/sdcard', data=data)
    assert response.status == 401
