# Development sites behind a proxy

## Redirects to the public hostname

In developer mode, Frappe can append the bench's `webserver_port` to a site's
`host_name` when that URL has no port. A proxy serving HTTPS on port 443 can then
redirect the browser to `https://dev.example.com:8000`, which the proxy does not serve.

Set the site's public URL with an explicit port:

```bash
pilot -b dev frappe --site site.localhost set-config host_name https://dev.example.com:443
```

Use the port that the browser reaches: `443` for standard HTTPS, `80` for standard
HTTP, or the proxy's custom port. Keep the bench's internal `webserver_port` unchanged.
Save the previous `host_name` so you can restore it when you remove the proxy.

This setting controls generated URLs. Configure site selection and Socket.IO
routing separately. Test a login redirect, an authenticated API call, and a
realtime event through the public hostname.
