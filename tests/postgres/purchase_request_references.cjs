/* Isolated PostgreSQL/WASM regression. Requires @electric-sql/pglite (test only).
 * node tests/postgres/purchase_request_references.cjs
 * Never connects to an operational database.
 */
const {PGlite} = require('@electric-sql/pglite');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');

(async () => {
  const db = new PGlite();
  try {
    await db.exec(`
      create role anon; create role authenticated; create role service_role bypassrls;
      create table users(id integer primary key);
      create table skus(id integer primary key);
      create table erp_purchase_orders(id uuid primary key);
      create table erp_vehicle_entries(id uuid primary key, item_number integer);
      create table erp_work_orders(id uuid primary key, vehicle_entry_id uuid,
          numero_os varchar, status varchar, technical_status varchar);
      insert into users values(1); insert into skus values(1);
    `);
    const migrations = path.resolve(__dirname, '../../supabase/migrations');
    await db.exec(fs.readFileSync(path.join(migrations,
      '20261006120000_purchase_requests.sql'), 'utf8'));
    const workOrders = new Map();
    for (const [item, number] of [[3185,'TA001793'],[3186,'TA001794'],
                                [3187,'TA001795'],[3187,'TA001796'],
                                [3188,'TA001797'],[3188,'OLD001797']]) {
      const entry = randomUUID(), id = randomUUID();
      await db.query('insert into erp_vehicle_entries values ($1,$2)', [entry,item]);
      await db.query("insert into erp_work_orders values ($1,$2,$3,$4,$5)",
                     [id,entry,number,number.startsWith('OLD')?'CANCELADA':'ATIVA',
                      number.startsWith('OLD')?'CONCLUIDA':'ABERTA']);
      workOrders.set(number, id);
    }
    const examples = [
      ['OS 3185', ['TA001793'], 'VINCULADA'],
      ['O.S. 3185 / 3186', ['TA001793','TA001794'], 'VINCULADA'],
      ['ITEM 3186', ['TA001794'], 'VINCULADA'],
      ['TA001793', ['TA001793'], 'VINCULADA'],
      ['OC 3185', [], 'SEM_MATCH'],
      ['O.S. TA001793', ['TA001793'], 'VINCULADA'],
      ['OS 3185, OC 3186', ['TA001793'], 'VINCULADA_PARCIAL'],
      ['3185; 3186', ['TA001793','TA001794'], 'VINCULADA'],
      ['PRODUÇÃO', [], 'SEM_REFERENCIA'],
      ['O.S. 9999', [], 'SEM_MATCH'],
      ['OS 3185 / 9999', ['TA001793'], 'VINCULADA_PARCIAL'],
      ['OS 3187', [], 'AMBIGUA'],
      ['O.S 3188', ['TA001797'], 'VINCULADA'],
      ['O.S. 3185 e 3186', ['TA001793','TA001794'], 'VINCULADA'],
      ['O.Ss - 3186 e 3185', ['TA001793','TA001794'], 'VINCULADA'],
      ['O.Ss; 3185, 3186', ['TA001793','TA001794'], 'VINCULADA'],
      ['OSs 3185, 3186', ['TA001793','TA001794'], 'VINCULADA'],
      ['ALMOXARIFADO', [], 'SEM_REFERENCIA'],
      ['-', [], 'SEM_REFERENCIA'],
      ['ADMINISTRATIVO', [], 'SEM_REFERENCIA'],
      ['', [], 'SEM_REFERENCIA'],
    ];
    for (const example of examples) {
      example.push(randomUUID());
      await db.query(`insert into erp_purchase_requests
        (id,origin,sku_id,sku_codigo,descricao,unidade,quantity,needed_at,reference,
         notes,status,requested_by_id,requested_by,created_at,updated_at,version,idempotency_key)
        values ($1,'ESTOQUE',1,'MAT-001','Material','PC',2,'2026-10-20',$2,
                '','SOLICITADA',1,'PAULO',now(),now(),1,$1)`, [example[3],example[0]]);
    }
    const migration = fs.readFileSync(path.join(migrations,
      '20261007120000_purchase_request_work_order_references.sql'), 'utf8');
    await db.exec(migration);
    for (const [reference, expected, result, requestId] of examples) {
      const links = await db.query(`select w.numero_os from erp_purchase_request_work_orders l
        join erp_work_orders w on w.id=l.work_order_id where l.request_id=$1`, [requestId]);
      assert.deepEqual(links.rows.map(r => r.numero_os).sort(), [...expected].sort(), reference);
      const report = await db.query(`select original_reference,result
        from erp_purchase_request_reference_backfill where request_id=$1`, [requestId]);
      assert.equal(report.rows[0].original_reference, reference);
      assert.equal(report.rows[0].result, result, reference);
    }
    const firstId = examples[0][3];
    await db.query("update erp_purchase_requests set sector='ADMINISTRATIVO' where id=$1", [firstId]);
    await db.query(`update erp_purchase_request_reference_backfill
      set result='REVISADA_MANUALMENTE',resolved_by='PAULO' where request_id=$1`, [firstId]);
    await db.exec(migration);
    const preserved = await db.query(`select r.sector,b.result from erp_purchase_requests r
      join erp_purchase_request_reference_backfill b on b.request_id=r.id where r.id=$1`, [firstId]);
    assert.equal(preserved.rows[0].sector, 'ADMINISTRATIVO');
    assert.equal(preserved.rows[0].result, 'REVISADA_MANUALMENTE');
    for (const role of ['anon','authenticated']) {
      for (const table of ['erp_purchase_request_work_orders','erp_purchase_request_reference_backfill']) {
        const privilege = await db.query('select has_table_privilege($1,$2,\'select\') as allowed',
                                         [role,table]);
        assert.equal(privilege.rows[0].allowed, false);
      }
    }
    await db.query('select pg_advisory_xact_lock(hashtextextended($1,0))',
                   ['purchase-request-material:1']);
    console.log(`PostgreSQL/WASM OK: ${examples.length} historical-reference cases, ` +
                'safe rerun, UUID links, private tables and transactional lock SQL.');
  } finally {
    await db.close();
  }
})().catch(error => {console.error(error);process.exitCode=1;});
