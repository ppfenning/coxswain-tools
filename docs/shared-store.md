# Choosing a store: files or shared

The default store is files, meaning a sqlite run store and local trace files.
It is the default so that brew and CI installs stay light and non-interactive.
The choice is recorded in settings, not in the brew formula, so changing it never means reinstalling.

The other choice is shared. It keeps run records in Postgres and traces in an S3-compatible object store, which is Garage in the recipe below.
You pick one with `cox setup --store files` or `cox setup --store shared`.
You move between them later with `cox setup store switch files` or `cox setup store switch shared`.
A Postgres store needs the extra: `pip install 'coxswain-tools[postgres]'`.

## Why choose shared on one machine

A shared store gives a single machine the same dashboards and the same backups as a fleet.
Nothing about the setup is specific to one box, so the tools behave the same on one machine as on ten.
When you add a second machine later, it points at the same store and no migration is needed.

Stay on files if you only want the tools to work and have no use for dashboards or backups yet.
Files needs no services, no credentials and no network.

## What shared sets up

Choosing shared writes three things: a storage URL, a traces URL and an object store block.
They go into a profile named `shared-store.yaml`, beside the settings file.
That profile is machine-local. Setup adds its name to the git exclude file, refuses to write it if git already tracks it, and creates it with owner-only permissions.
The choice itself is recorded in settings as `store.mode`.

The storage URL in that file contains the Postgres user and password. Treat the file as a secret.
Owner-only permissions and the git exclude exist for that reason. Do not copy it into a repository, a chat or a ticket.
The traces URL names a bucket. The object store block holds the endpoint, the region and the names of two environment variables, and holds no keys.

Setup reads the Postgres password and the S3 keys from environment variables that you name when you run it.
It never prints the values.
The object store block then points at two fixed variable names, `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`.
Export the S3 key pair under those two names in every shell that runs cox against the shared store, because the keys are read from the environment each time and are not stored in the profile.

What the tools print is limited too. The setup summary names the store scheme, host, port and bucket, and never the user, password or keys.
The doctor row shows neither the URL nor its credentials.

## Switching in each direction

Run the switch verb with the store you want to end up on:

```bash
cox setup store switch shared
cox setup store switch files
```

The switch copies the existing sqlite store and the local trace files into the target store.
It leaves the source in place. Nothing is deleted or truncated in the store you came from, so you can switch back or inspect it.
A repeated switch is safe. The copy never drops anything, so running a switch again after an interrupted one is safe, and a second complete run reports zero newly copied.
If the store is already on the backend you name, the switch prints that there is nothing to do.

The same choice is available when you first set up, through `cox setup --store files` and `cox setup --store shared`.

## Checking the choice with the setup doctor

`cox setup doctor` verifies the store you chose. It does this in its `store` row.
The row shows the kind of store and the number of runs in it, for example `sqlite, 12 runs` or `postgresql, 12 runs`.
When the store does not answer, the row fails and gives the reason. An unreachable host fails after at most five seconds.
The row never shows the URL or its credentials.

On a fresh files install the sqlite store does not exist yet, because the first run creates it.
The row then reads `no store yet (it is created by the first run)` and fails, so doctor exits 1 until you have launched once.
`docs/getting-started.md` walks through that first run.

## What brew and the installer do not do

Brew and the installer never start Postgres or Garage.
They install the tools and record your store choice. Running the services is your job, and running them for you is out of scope for this project.

If you want a local Postgres and Garage to try shared on one machine, the recipe below is an example you run yourself.
Both services are published on the loopback address only, so they are reachable only from this machine.
Credentials come from environment variables in your shell, and none are written into the files. Export them first and keep the values, because Postgres and Garage need the same ones on every start:

```bash
export POSTGRES_PASSWORD="$(openssl rand -hex 16)"
export GARAGE_RPC_SECRET="$(openssl rand -hex 32)"
export GARAGE_ADMIN_TOKEN="$(openssl rand -hex 32)"
```

Save this as `compose.yaml`:

```yaml
# Example only. You run this yourself; the tools never start it.
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: cox
      POSTGRES_DB: cox
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - cox-postgres:/var/lib/postgresql/data

  garage:
    image: dxflrs/garage:v1.0.1
    environment:
      GARAGE_RPC_SECRET: ${GARAGE_RPC_SECRET:?set GARAGE_RPC_SECRET}
      GARAGE_ADMIN_TOKEN: ${GARAGE_ADMIN_TOKEN:?set GARAGE_ADMIN_TOKEN}
    ports:
      - "127.0.0.1:3900:3900"
      - "127.0.0.1:3903:3903"
    volumes:
      - ./garage.toml:/etc/garage.toml:ro
      - cox-garage-meta:/var/lib/garage/meta
      - cox-garage-data:/var/lib/garage/data

volumes:
  cox-postgres:
  cox-garage-meta:
  cox-garage-data:
```

Save this beside it as `garage.toml`. It holds no secret, because Garage reads the RPC secret and the admin token from the two variables above.
The services listen on all interfaces inside their containers, and the published ports above are what keep them on loopback.

```toml
metadata_dir = "/var/lib/garage/meta"
data_dir = "/var/lib/garage/data"
db_engine = "sqlite"
replication_factor = 1

rpc_bind_addr = "0.0.0.0:3901"
rpc_public_addr = "127.0.0.1:3901"

[s3_api]
s3_region = "garage"
api_bind_addr = "0.0.0.0:3900"

[admin]
api_bind_addr = "0.0.0.0:3903"
```

Start both with `docker compose up -d`. Then give Garage a layout, a bucket and a key with its command line. Replace `<node id>` with the id that `status` prints:

```bash
docker compose exec garage /garage status
docker compose exec garage /garage layout assign -z dc1 -c 1G <node id>
docker compose exec garage /garage layout apply --version 1
docker compose exec garage /garage bucket create cox-traces
docker compose exec garage /garage key create cox-key
docker compose exec garage /garage bucket allow --read --write --owner cox-traces --key cox-key
```

`key create` prints a key id and a secret once. Export them as `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, the two names the profile points at.
For `cox setup --store shared`, give setup the Postgres host, port and database from the compose file, the bucket `cox-traces`, the region `garage`, and the endpoint on the loopback address at port 3900.
Tell setup the names of the environment variables that hold the Postgres password and the S3 keys, not their values.
