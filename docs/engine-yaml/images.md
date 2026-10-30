# Rendered images and HTTP requests

[Guide index](README.md) · [Complete synthetic example](examples/image_captcha.yaml)

`capture_image` captures the rendered appearance of a target through the observer.
Images and canvas elements appear in PageState even without a click handler. Hidden
decorative images are omitted; pixels and image source URLs are not in PageState.

```yaml
actions:
  - wait_for: {target: picture, state: visible, timeout_s: 10}
  - capture_image: {target: picture, into: pixels}
  - fetch: {request: read_image, into: answer}
  - capture_image:
      target: picture
      into: checked_pixels
      expected_sha256: '{vars.pixels.sha256}'
```

`vars.pixels` contains `data_url` (a Secret PNG data URL), `mime_type`, `width`,
`height`, and `sha256`. The observer captures browser-rendered pixels, including
CSS composites, cross-origin images, tainted canvases and frames. It does not fetch
the DOM image source again. Target the wrapper when several elements form the image.

Captures must fit the viewport, contain at most 2 million CSS pixels and encode to
at most 4 million data URL characters. Missing/hidden/unloaded images, detected
occlusion, clipping or movement fail explicitly. Occlusion uses sampled points;
authors must supply an appropriate readiness condition for asynchronous content.
Failure clears the destination variable so stale pixels cannot be reused.

Send the image through an ordinary `kind: http` graph request. URL, credentials,
model name, prompt and response extraction are graph inputs/data. The engine knows
nothing about a particular model, model server or purpose of the image. A sandboxed
`script` transforms the response; it has no network or filesystem access.

For an OpenAI-compatible endpoint such as a separately managed llama.cpp server:

```yaml
requests:
  read_image:
    kind: http
    method: POST
    url: '{vision_url}'
    json:
      model: '{vision_model}'
      messages:
        - role: user
          content:
            - {type: text, text: 'Transcribe the text in this image.'}
            - type: image_url
              image_url: {url: '{vars.pixels.data_url}'}
      temperature: 0
    extract:
      text: {json: 'choices[0].message.content'}
    sensitive: true
    cache: none
    timeout_s: 60
    retry: {attempts: 1}
```

The caller supplies `vision_url` and `vision_model` and any required authentication.
Reference `vars.answer.text` after fetching once; referencing `requests.read_image`
again can issue another request. Validate the returned text in the graph before use.
Configure the caller's host allowlist to include the endpoint. Container loopback
addresses refer to the container, not a model service on the host.

The synthetic example tests image transport, freshness checks, redaction and bounded
rejection handling with a mocked service; it makes no claim about model accuracy.
Test requests in a builder session after capture. Restart the builder API and open
a new browser session after updating observer code.
