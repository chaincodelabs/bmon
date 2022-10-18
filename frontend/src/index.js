import { useState, useEffect } from 'preact/hooks';
import React from 'preact/compat';
import { render } from 'preact';
import { DateTime } from 'luxon';

import './assets/styles/style';
import { postData, get_data } from './util';

import { map } from 'lodash';

const App = (props) => {
  var children;
  const path = window.location.pathname;

  if (path.match(/.*/)) {
    children = <Home />;
  }
  return (
    <div id="app">
      <div className="header" href="/">
        <div id="bmon-title"> 
          bmon
        </div>
      </div>
      
      <div id="content">
        {children}
      </div>
    </div>
  );
};

export const Home = (props) => {
  const [ hosts, sethosts ] = useState(null);
  const [ blocks, setblocks ] = useState(null);

  useEffect(() => get_data(`/api/hosts`, (resp) => sethosts(resp)), [0]);
  useEffect(() => get_data(`/api/blocks`, (resp) => setblocks(resp)), [0]);

  return <>
    <div className="block-connects">
      {map(blocks, (b) => <BlockCard key={b.height} {...b} />)}
    </div>

    <div className="hosts">
      {map(hosts, (h) => <HostCard key={h.name} {...h} />)}
    </div>
  </>;
};


const CardStat = (props) => (
  <div className={`stat ${props.className || ''}`}>
    <div className="title">{props.title}</div>
    <div className="value">{props.children}</div>
  </div>
);

const Card = (props) => {
  return <div className={`card ${props.className || ''}`}>
    <div className="card-title">
      {props.title}
    </div>

    <div className="stats">
      {props.children}
    </div>
  </div>;
};


const BlockCard = (props) => {
  const dt = DateTime.fromISO(props.date);
  const mindt = DateTime.fromISO(props.min_dt);
  const fmtfloat = (flt) => Number.parseFloat(flt).toFixed(4);
  const items = {
    "date": <>{dt.toFormat('LLL dd')}<br />{dt.toFormat('HH:mm:ss.uuu')}</>,
    "saw at": <>{mindt.toFormat('LLL dd')}<br />{mindt.toFormat('HH:mm:ss.uuu')}</>,
    "saw block diffs": <>
        <table>
        {map(props.diffs, (diff, host) => 
            <tr><td>{host}</td><td>{fmtfloat(diff)} sec</td></tr>
        )}
        </table>
    </>,
    "stddev": <>± {fmtfloat(props.stddev_got_time)} sec</>,
  };

  return <Card className='block-connect' title={`block ${props.height}`}>
    {map(items, (v, k) => <CardStat title={k} children={v} />)}
  </Card>;
};


const HostCard = (props) => {
  console.log(props);
  const items = {
    "version": <>{props.bitcoin_version}</>,
    "height": <>{props.chaininfo.blocks}</>,
    "pruned?": <>{props.chaininfo.pruned}</>,
    "peers": <>
        <table>
        {map(props.peers, (addr, subver) => 
            <tr><td>{addr}</td><td>{subver}</td></tr>
        )}
        </table>
    </>,
  };
  const chain = props.chaininfo.chain;
  const net = chain == 'main' ? '' : `(${chain})`;
  return <Card className='host' title={`host ${props.name} ${net}`}>
    {map(items, (v, k) => <CardStat title={k} children={v} />)}
  </Card>;
};



render(<App />, document.body);
