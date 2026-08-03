from hub_portal.release import get_release_id


def release_state(request):
    return {
        "hub_release_id": get_release_id(),
    }
