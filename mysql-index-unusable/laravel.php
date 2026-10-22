<?php

require __DIR__ . '/vendor/autoload.php';

use Illuminate\Database\Capsule\Manager as Capsule;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Schema\Blueprint;

class Store extends Model
{
    protected $table = 'stores';
    public $timestamps = false;

    public function members()
    {
        return $this->hasMany(Member::class, 'store_id');
    }
}

class Member extends Model
{
    protected $table = 'members';
    public $timestamps = false;
}

function connect(string $charset, string $collation): Capsule
{
    $capsule = new Capsule();
    $capsule->addConnection([
        'driver' => 'mysql',
        'host' => 'mysql',
        'database' => 'lab',
        'username' => 'root',
        'password' => '',
        'charset' => $charset,
        'collation' => $collation,
    ]);
    $capsule->setAsGlobal();
    $capsule->bootEloquent();
    return $capsule;
}

function show(string $label, $value): void
{
    echo $label, ': ', var_export($value, true), "\n";
}

function run_query($query): void
{
    show('sql', $query->toSql());
    show('bindings', $query->getBindings());
    show('rows', $query->get()->count());
}

$step = $argv[1] ?? '';
$capsule = connect('utf8mb4', 'utf8mb4_unicode_ci');
$pdo = $capsule->getConnection()->getPdo();
show('PDO::ATTR_EMULATE_PREPARES', $pdo->getAttribute(PDO::ATTR_EMULATE_PREPARES));

switch ($step) {
    case 'where-string':
        run_query(Capsule::table('members')->where('code', '1000042'));
        break;
    case 'where-int':
        run_query(Capsule::table('members')->where('code', 1000042));
        break;
    case 'has-many':
        $store = Store::find(42);
        show('store key', $store->getKey());
        run_query($store->members());
        break;
    case 'where-date':
        run_query(Capsule::table('members')->whereDate('created_at', '2010-01-01'));
        break;
    case 'create-table':
        foreach ([['utf8', 'utf8_unicode_ci'], ['utf8mb4', 'utf8mb4_unicode_ci']] as [$charset, $collation]) {
            $capsule = connect($charset, $collation);
            $queries = $capsule->getConnection()->pretend(function () use ($capsule) {
                $capsule->schema()->create('accounts', function (Blueprint $table) {
                    $table->id();
                    $table->string('code');
                });
            });
            show("config charset=$charset collation=$collation", array_column($queries, 'query'));
        }
        break;
    default:
        fwrite(STDERR, "unknown step: $step\n");
        exit(2);
}
