"""Controls for a specifically selected desktop companion, through dispatch."""
from aria import runtime
from aria.outcome import done, failed, unverified


def tool_desktop_control(args):
    from aria.node_hub import HUB, NodeHubError
    node, action = str(args.get('node', '')), str(args.get('action', ''))
    record = next((n for n in HUB.nodes() if n['name'] == node), None)
    if not record or record.get('kind') != 'desktop':
        return failed('desktop_control', 'Select a connected desktop by its exact node name')
    if action not in record.get('capabilities', []):
        return failed('desktop_control', 'This desktop has not enabled that capability')
    arguments = dict(args.get('arguments') or {})
    arguments['_execution_id'] = runtime.operation_id() or runtime.current().id
    try:
        result = HUB.send_command(node, action, arguments, timeout=30)
    except NodeHubError as exc:
        if exc.code in {'timeout','disconnected','node_error'}:
            return unverified('desktop_control', str(exc))
        return failed('desktop_control', str(exc))
    if result.get('image_base64'):
        import base64
        from aria.vision import vision_first_look
        return vision_first_look(base64.b64decode(result['image_base64']),
                                 args.get('question', 'Describe this remote screen'),
                                 result.get('mime', 'image/png'))
    import json
    return done('desktop_control', node=node, action_name=action, receipt=json.dumps(result))
